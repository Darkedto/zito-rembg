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
import os

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
