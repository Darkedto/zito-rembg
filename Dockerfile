# ════════════════════════════════════════════════════════════════════
# Zito SmartBuy — recorte de fondo con rembg (CPU, sin GPU)
#
# Sirve igual en Hugging Face Spaces (Docker), Google Cloud Run, Fly.io o
# un VPS: escucha en $PORT y por defecto en 7860, que es el que espera HF.
#
#   docker build -t zito-rembg .
#   docker run --rm -p 7860:7860 zito-rembg
# ════════════════════════════════════════════════════════════════════
FROM python:3.11-slim

# libgl1 / libglib: los pide opencv, que rembg usa para limpiar la máscara.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# Versiones exactas, todas verificadas juntas (2026-08-18). Se fijan a
# proposito: con rangos abiertos el build no es reproducible y ya nos
# mordio dos veces - una version nueva de rembg cambio como recibe
# `sess_opts` (TypeError al arrancar) y otra subio el minimo de pillow a
# 12.1, chocando con el "pillow<12" que estaba puesto aca (el build moria
# con "dependency conflicts"). NO poner rango en pillow: la version la
# decide rembg, que es quien la necesita.
RUN pip install --no-cache-dir \
      "rembg[cpu]==2.0.81" \
      "fastapi==0.141.1" \
      "uvicorn[standard]==0.52.3"

# El modelo queda HORNEADO en la imagen. Si se bajara en la primera
# petición, el primer recorte tardaría un minuto y podría fallar si el
# hosting no tiene salida a internet.
ENV U2NET_HOME=/opt/modelos
RUN mkdir -p /opt/modelos \
 && python -c "from rembg import new_session; new_session('u2netp')" \
 && chmod -R a+rX /opt/modelos

WORKDIR /app
COPY app.py /app/app.py

# Hugging Face Spaces corre el contenedor como el usuario 1000.
RUN useradd -m -u 1000 zito || true
USER 1000

ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
