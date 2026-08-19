# ════════════════════════════════════════════════════════════════════
# Zito SmartBuy — recorte de fondo (U²-Net "p" en ONNX, CPU)
#
# Sin la libreria `rembg`: solo onnxruntime + numpy + pillow. Ver el
# comentario largo al inicio de app.py — rembg arrastraba pymatting/numba/
# scikit-image (+117 MB de RAM que no se usan) y el contenedor se pasaba
# de los 512 MB del plan gratis de Render.
#
# Escucha en $PORT (Render lo define solo; 7860 por defecto).
#
#   docker build -t zito-rembg .
#   docker run --rm -p 7860:7860 zito-rembg
# ════════════════════════════════════════════════════════════════════
# 3.12 y no 3.11: numpy 2.5.x exige Python >=3.12 (no publica wheel para
# 3.11) y el build moria en el pip install. Si se cambia esta linea, hay
# que revisar que las 5 versiones de abajo tengan wheel para ESA version
# de Python — verificar en pypi.org, no en la maquina de uno, que casi
# nunca corre la misma version que el contenedor.
FROM python:3.12-slim

# Versiones exactas, con wheel confirmado para cp312-linux: con rangos
# abiertos el build no es reproducible y ya nos mordio (una version nueva
# subio el minimo de pillow y el build murio con "dependency conflicts").
RUN pip install --no-cache-dir \
      "onnxruntime==1.29.0" \
      "numpy==2.5.2" \
      "pillow==12.3.0" \
      "fastapi==0.141.1" \
      "uvicorn[standard]==0.52.3"

# El modelo queda HORNEADO en la imagen (4.5 MB). Si se bajara en la
# primera peticion, el primer recorte tardaria y dependeria de que el
# hosting tenga salida a internet.
ADD https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx /opt/modelos/u2netp.onnx
RUN chmod -R a+rX /opt/modelos
ENV MODELO_PATH=/opt/modelos/u2netp.onnx

# Menos memoria en Linux: glibc crea una "arena" de malloc por hilo y eso
# infla el RSS; y las librerias numericas levantan pools de hilos que no
# sirven de nada con la CPU del plan gratis.
ENV MALLOC_ARENA_MAX=2
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1

WORKDIR /app
COPY app.py /app/app.py

RUN useradd -m -u 1000 zito || true
USER 1000

ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
