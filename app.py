# ════════════════════════════════════════════════════════════════════
# Zito SmartBuy — servicio de recorte de fondo (rembg)
#
# Recibe una foto de producto y devuelve un PNG con el fondo transparente.
# No recorta ni arma el cuadrado: de eso se sigue encargando el teléfono
# (ZitoFotos.procesarIA reusa el MISMO camino que el recorte local — caja
# del contenido, cuadrado de 800 px, WebP). Así el servidor hace una sola
# cosa y se puede cambiar por otro sin tocar la app.
#
# El modelo (ISNet / U²-Net, licencia permisiva) queda dentro de la imagen
# de Docker, así que el arranque no depende de bajar nada de internet.
#
# Variables de entorno:
#   PORT            puerto (7860 por defecto = el que espera Hugging Face)
#   REMBG_MODELO    u2netp (default, liviano — pensado para 512 MB de RAM,
#                   el límite del plan gratis de Render/HF) · u2net ·
#                   isnet-general-use (mejor calidad, pero pide ~1 GB+ de RAM;
#                   sirve en un VPS propio o un plan de pago, no en el free tier)
#   REMBG_TOKEN     si se define, exige la cabecera X-Zito-Token con ese valor
#   REMBG_MAX_PX    lado máximo al que se reduce antes de procesar (1400)
#   REMBG_MAX_BYTES tope del archivo que se acepta (8 MB)
# ════════════════════════════════════════════════════════════════════
import gc
import io
import os

import onnxruntime as ort
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from rembg import new_session, remove

MODELO     = os.environ.get("REMBG_MODELO", "u2netp")
TOKEN      = os.environ.get("REMBG_TOKEN", "")
MAX_PX     = int(os.environ.get("REMBG_MAX_PX", "1000"))
MAX_BYTES  = int(os.environ.get("REMBG_MAX_BYTES", str(8 * 1024 * 1024)))

# Medido a mano (2026-08-18): con la configuración por defecto de
# onnxruntime, un solo recorte llega a pedir ~465 MB de RAM — casi todo el
# plan gratis de Render/HF (512 MB), y eso sin contar FastAPI ni el sistema
# operativo del contenedor. La "arena" de memoria de onnxruntime reserva de
# más sin que tenga que ver con el tamaño de la imagen (se probó a distintas
# resoluciones y el pico casi no cambiaba). Desactivarla + limitar los hilos
# a 1 bajó el pico a ~380 MB — deja margen real. No usar SessionOptions()
# "a mano" sin esto en un plan de 512 MB: el deploy se cae con
# "Ran out of memory".
_SESS_OPTS = ort.SessionOptions()
_SESS_OPTS.enable_cpu_mem_arena = False
_SESS_OPTS.enable_mem_pattern = False
_SESS_OPTS.intra_op_num_threads = 1
_SESS_OPTS.inter_op_num_threads = 1

app = FastAPI(title="Zito · recorte de fondo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# Se carga UNA vez al arrancar: la primera foto no paga la carga del modelo.
sesion = new_session(MODELO, sess_opts=_SESS_OPTS)


@app.get("/")
def salud():
    return {"ok": True, "modelo": MODELO, "max_px": MAX_PX}


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

    # Reducir antes de procesar: en CPU el tiempo sube con el cuadrado del lado.
    img.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)

    # `matting=1` afina el borde (pelos, transparencias) a costa de bastante
    # más CPU. Para una botella o una caja no hace falta.
    matting = req.query_params.get("matting") in ("1", "true", "si")
    salida = remove(
        img,
        session=sesion,
        post_process_mask=True,
        alpha_matting=matting,
    )

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
        headers={"X-Zito-Modelo": MODELO},
    )
