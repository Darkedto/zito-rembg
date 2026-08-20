---
title: Zito Recorte De Fondo
emoji: 🖼️
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# Zito SmartBuy — recorte de fondo (rembg)

Servicio mínimo que recibe una foto de producto y devuelve un **PNG con el
fondo transparente**. Lo usa la app de tiendas (`zitosmartbuy.html`) a través
de la Edge Function `quitar-fondo` de Supabase — el celular nunca habla
directo con este servidor, así que la URL y el token viven como secretos.

Modelo: **U²-Net "p"** (`u2netp`, licencia permisiva — uso comercial
libre, a diferencia de BRIA RMBG, que es no-comercial). Se corre el ONNX
**directo con onnxruntime**, sin la librería `rembg`: ella arrastra
`pymatting`/`numba`/`scikit-image` (+117 MB de RAM que este servicio no
usa) y el contenedor se pasaba de los 512 MB del plan gratis de Render.
Medido: base 90 MB, pico 285 MB por recorte — antes eran 210 y 430 MB.
El preprocesado replica exactamente el de rembg para u2netp, así que el
recorte sale igual.

## Probarlo en tu computadora

```bash
docker build -t zito-rembg rembg-server
docker run --rm -p 7860:7860 zito-rembg
curl -s -X POST --data-binary @foto.jpg http://localhost:7860/quitar-fondo -o recorte.png
```

## Publicarlo

**Hugging Face Space (gratis).** Crear un Space nuevo con SDK **Docker** y
subir `Dockerfile`, `app.py` y este `README.md` (el frontmatter de arriba es
la configuración del Space). Queda en
`https://USUARIO-zito-recorte-de-fondo.hf.space`. Se duerme tras unos días
sin uso y el primer recorte después tarda ~40 s.

**Google Cloud Run.** Escala a cero y aguanta bien la capa gratuita:

```bash
gcloud run deploy zito-rembg --source rembg-server --region us-central1 --memory 2Gi --allow-unauthenticated
```

**VPS propio.** `docker run -d --restart=always -p 7860:7860 -e REMBG_TOKEN=... zito-rembg` detrás de nginx con HTTPS.

## Endpoints

| | |
|---|---|
| `GET /` | salud: `{ok, modelo, max_px}` |
| `POST /quitar-fondo` | cuerpo = bytes de la imagen · respuesta = PNG con alfa |
| `POST /quitar-fondo` con `Content-Type: application/json` | cuerpo = `{"url": "https://…"}` · el servidor **baja la imagen** y devuelve el PNG. Para proyectos que sacan la foto de internet: hacerlo en el navegador rompe por CORS al leer los pixeles del canvas. |
| `POST /quitar-fondo?matting=1` | borde más fino (pelo, transparencias); bastante más lento |

Si `REMBG_TOKEN` está definido, hay que mandar la cabecera `X-Zito-Token`.

## Variables

| Variable | Default | Para qué |
|---|---|---|
| `PORT` | 7860 | puerto (Cloud Run lo pone en 8080 solo) |
| `MODELO_PATH` | `/opt/modelos/u2netp.onnx` | ruta del .onnx horneado en la imagen |
| `REMBG_TOKEN` | *(vacío)* | exige `X-Zito-Token` |
| `REMBG_MAX_PX` | 1400 | lado máximo antes de procesar |
| `REMBG_MAX_BYTES` | 8388608 | tope del archivo |
| `REMBG_URL_OFF` | *(vacío)* | `1` apaga la descarga por URL |
| `REMBG_URL_TIMEOUT` | 20 | segundos para bajar la imagen |
| `REMBG_USER_AGENT` | *(uno propio)* | algunos sitios rechazan agentes desconocidos |

### Sobre el modo URL

El servidor está abierto en internet, así que bajar cualquier URL que le
manden sería un agujero (**SSRF**): alguien podría usarlo para tocar
direcciones internas de la red donde corre. Por eso `descargar_imagen()`
exige http/https, resuelve el nombre y **rechaza toda dirección privada,
loopback, link-local o reservada** — revalidando en cada redirección —,
corta la descarga en `REMBG_MAX_BYTES` y verifica que el `Content-Type`
sea una imagen.

Ojo con **Wikimedia**: rechaza (400) las URLs de miniatura con tamaños
arbitrarios (`.../320px-foo.jpg`) con el mensaje "Use thumbnail sizes
listed on…". Las URLs del archivo original sí funcionan. No es un
problema del servidor.
