# ════════════════════════════════════════════════════════════════════
# Zito SmartBuy — servicio de recorte de fondo (U²-Net "p", ONNX)
#
# Recibe una foto de producto y devuelve un PNG con el fondo transparente.
# No recorta ni arma el cuadrado: de eso se sigue encargando el teléfono
# (ZitoFotos.procesarIA reusa el MISMO camino que el recorte local — caja
# del contenido, cuadrado de 800 px, WebP). Así el servidor hace una sola
# cosa y se puede cambiar por otro sin tocar la app.
#
# ── Por qué NO se usa la librería `rembg` ────────────────────────────
# Se empezó con rembg y hubo que sacarla por dos razones, ambas medidas:
#
#   1) MEMORIA. El plan gratis de Render da 512 MB para TODO el contenedor.
#      rembg arrastra `pymatting` (→ numba → llvmlite), `scikit-image` y
#      `scipy`, que llevan la memoria base de 53 MB a 170 MB antes de
#      cargar el modelo — 117 MB de librerías que este servicio nunca usa
#      (pymatting es solo para el modo alpha-matting). Con eso, un solo
#      recorte se pasaba de 512 MB y Render mataba el contenedor (502).
#
#   2) FRAGILIDAD DE VERSIONES. Dos deploys se cayeron por cambios internos
#      entre versiones de rembg (cómo recibe `sess_opts`, y el mínimo de
#      pillow que exige).
#
# Acá se corre el modelo ONNX directo: solo onnxruntime + numpy + pillow.
# El preprocesado es EL MISMO que hace rembg para u2netp (resize 320x320
# LANCZOS, normalización ImageNet), así que el recorte sale igual.
#
# Variables de entorno:
#   PORT            puerto (10000 en Render; 7860 por defecto)
#   MODELO_PATH     ruta del .onnx (viene horneado en la imagen)
#   REMBG_TOKEN     si se define, exige la cabecera X-Zito-Token
#   REMBG_MAX_PX    lado máximo al que se reduce antes de procesar (1000)
#   REMBG_MAX_BYTES tope del archivo que se acepta (8 MB)
# ════════════════════════════════════════════════════════════════════
import gc
import io
import ipaddress
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image

MODELO_PATH = os.environ.get("MODELO_PATH", "/opt/modelos/u2netp.onnx")
TOKEN       = os.environ.get("REMBG_TOKEN", "")
MAX_PX      = int(os.environ.get("REMBG_MAX_PX", "1000"))
MAX_BYTES   = int(os.environ.get("REMBG_MAX_BYTES", str(8 * 1024 * 1024)))
# Descarga por URL (para proyectos que sacan la foto del producto de
# internet en vez de la cámara). Se puede apagar con REMBG_URL_OFF=1.
URL_OFF     = os.environ.get("REMBG_URL_OFF") == "1"
URL_ESPERA  = int(os.environ.get("REMBG_URL_TIMEOUT", "20"))
URL_AGENTE  = os.environ.get(
    "REMBG_USER_AGENT",
    "Mozilla/5.0 (compatible; ZitoRecorte/1.0; +https://zito-rembg.onrender.com)",
)

# U²-Net entra siempre a 320x320 y normaliza con las medias de ImageNet.
ENTRADA = (320, 320)
MEDIA   = (0.485, 0.456, 0.406)
DESVIO  = (0.229, 0.224, 0.225)

app = FastAPI(title="Zito · recorte de fondo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# Opciones de memoria: la "arena" de onnxruntime reserva de más por diseño
# (medido: sin desactivarla el pico sube ~85 MB, sin relación con el tamaño
# de la foto). Un solo hilo, porque el plan gratis da 0.1 CPU igual.
_opts = ort.SessionOptions()
_opts.enable_cpu_mem_arena = False
_opts.enable_mem_pattern = False
_opts.intra_op_num_threads = 1
_opts.inter_op_num_threads = 1

sesion = ort.InferenceSession(
    MODELO_PATH, sess_options=_opts, providers=["CPUExecutionProvider"]
)
NOMBRE_ENTRADA = sesion.get_inputs()[0].name


def _host_publico(host: str) -> None:
    """Rechaza direcciones internas.

    Este servidor está abierto en internet: si bajara cualquier URL que le
    manden, alguien podría usarlo para tocar direcciones privadas de la red
    donde corre (169.254.169.254 de los metadatos del hosting, 127.0.0.1,
    10.x, etc.). Eso se llama SSRF. Se resuelve el nombre y se exige que
    TODAS sus direcciones sean públicas.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo resolver esa dirección.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise HTTPException(status_code=400, detail="Esa dirección no está permitida.")


class _SinSaltosRaros(urllib.request.HTTPRedirectHandler):
    """Revalida el destino en CADA redirección (si no, la primera podría ser
    pública y la segunda apuntar a una dirección interna)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        partes = urllib.parse.urlsplit(newurl)
        if partes.scheme not in ("http", "https"):
            return None
        _host_publico(partes.hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def descargar_imagen(url: str) -> bytes:
    if URL_OFF:
        raise HTTPException(status_code=403, detail="La descarga por URL está apagada.")
    partes = urllib.parse.urlsplit((url or "").strip())
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise HTTPException(status_code=400, detail="La URL no es válida (debe ser http o https).")
    _host_publico(partes.hostname)

    pedido = urllib.request.Request(url, headers={
        "User-Agent": URL_AGENTE,       # muchos sitios rechazan al agente por defecto
        "Accept": "image/*,*/*;q=0.8",
    })
    abridor = urllib.request.build_opener(_SinSaltosRaros())
    try:
        with abridor.open(pedido, timeout=URL_ESPERA) as r:
            tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            largo = r.headers.get("Content-Length")
            if largo and int(largo) > MAX_BYTES:
                raise HTTPException(status_code=413, detail="La imagen pesa demasiado.")
            # Se lee de a poco y se corta: el Content-Length puede mentir o faltar.
            datos = r.read(MAX_BYTES + 1)
    except HTTPException:
        raise
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail="La página devolvió %s al pedir la imagen." % e.code)
    except Exception as e:
        raise HTTPException(status_code=502, detail="No se pudo bajar la imagen: %s" % e)

    if len(datos) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="La imagen pesa demasiado.")
    if not datos:
        raise HTTPException(status_code=502, detail="La URL no devolvió nada.")
    if tipo and not tipo.startswith("image/"):
        raise HTTPException(status_code=400, detail="Esa URL no es una imagen (es %s)." % tipo)
    return datos


def mascara_de(img: Image.Image) -> Image.Image:
    """Devuelve la máscara en escala de grises (255 = producto)."""
    chico = img.convert("RGB").resize(ENTRADA, Image.Resampling.LANCZOS)

    arr = np.array(chico, dtype=np.float32)
    arr /= max(float(arr.max()), 1e-6)
    for c in range(3):
        arr[:, :, c] = (arr[:, :, c] - MEDIA[c]) / DESVIO[c]
    tensor = np.expand_dims(arr.transpose(2, 0, 1), 0).astype(np.float32)

    pred = sesion.run(None, {NOMBRE_ENTRADA: tensor})[0][:, 0, :, :]
    mi, ma = float(pred.min()), float(pred.max())
    pred = (pred - mi) / max(ma - mi, 1e-6)
    pred = np.squeeze(pred)

    mask = Image.fromarray((pred * 255).astype("uint8"), mode="L")
    return mask.resize(img.size, Image.Resampling.LANCZOS)


@app.get("/")
def salud():
    return {"ok": True, "modelo": "u2netp", "max_px": MAX_PX}


@app.post("/quitar-fondo")
async def quitar_fondo(req: Request):
    if TOKEN and req.headers.get("x-zito-token") != TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")

    # Dos formas de mandar la foto:
    #   1) los bytes crudos en el cuerpo (la cámara — así la manda Zito)
    #   2) {"url": "https://..."} y la baja el servidor (para proyectos que
    #      sacan la foto del producto de internet: hacerlo en el navegador
    #      chocaría con CORS al leer los pixeles del canvas)
    tipo_ct = (req.headers.get("content-type") or "").split(";")[0].strip().lower()
    if tipo_ct == "application/json":
        try:
            datos = await req.json()
        except Exception:
            raise HTTPException(status_code=400, detail="El JSON no se pudo leer.")
        crudo = descargar_imagen((datos or {}).get("url", ""))
    else:
        crudo = await req.body()

    if not crudo:
        raise HTTPException(status_code=400, detail="No llegó ninguna imagen.")
    if len(crudo) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="La imagen pesa demasiado.")

    try:
        img = Image.open(io.BytesIO(crudo))
        img.load()
        img = img.convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="El archivo no es una imagen.")

    # Reducir antes de procesar: menos memoria y menos tiempo de CPU.
    img.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)

    salida = img.convert("RGBA")
    salida.putalpha(mascara_de(img))

    buf = io.BytesIO()
    salida.save(buf, format="PNG", optimize=True)
    cuerpo = buf.getvalue()

    # Suelta las imágenes intermedias ya: en un contenedor de 512 MB no
    # conviene esperar al ciclo normal del recolector para la próxima foto.
    del img, salida, buf, crudo
    gc.collect()

    return Response(
        content=cuerpo,
        media_type="image/png",
        headers={"X-Zito-Modelo": "u2netp"},
    )
