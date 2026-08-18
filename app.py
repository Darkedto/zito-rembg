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
#   REMBG_MODELO    isnet-general-use (default) · u2net · u2netp (más liviano)
#   REMBG_TOKEN     si se define, exige la cabecera X-Zito-Token con ese valor
#   REMBG_MAX_PX    lado máximo al que se reduce antes de procesar (1400)
#   REMBG_MAX_BYTES tope del archivo que se acepta (8 MB)
# ════════════════════════════════════════════════════════════════════
import io
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from rembg import new_session, remove

MODELO     = os.environ.get("REMBG_MODELO", "isnet-general-use")
TOKEN      = os.environ.get("REMBG_TOKEN", "")
MAX_PX     = int(os.environ.get("REMBG_MAX_PX", "1400"))
MAX_BYTES  = int(os.environ.get("REMBG_MAX_BYTES", str(8 * 1024 * 1024)))

app = FastAPI(title="Zito · recorte de fondo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# Se carga UNA vez al arrancar: la primera foto no paga la carga del modelo.
sesion = new_session(MODELO)


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
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"X-Zito-Modelo": MODELO},
    )
