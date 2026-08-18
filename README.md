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

Modelo por defecto: **`isnet-general-use`** (ISNet/U²-Net, licencia
permisiva — uso comercial libre, a diferencia de BRIA RMBG, que es
no-comercial).

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
| `POST /quitar-fondo?matting=1` | borde más fino (pelo, transparencias); bastante más lento |

Si `REMBG_TOKEN` está definido, hay que mandar la cabecera `X-Zito-Token`.

## Variables

| Variable | Default | Para qué |
|---|---|---|
| `PORT` | 7860 | puerto (Cloud Run lo pone en 8080 solo) |
| `REMBG_MODELO` | `isnet-general-use` | `u2net`, `u2netp` (más liviano y rápido) |
| `REMBG_TOKEN` | *(vacío)* | exige `X-Zito-Token` |
| `REMBG_MAX_PX` | 1400 | lado máximo antes de procesar |
| `REMBG_MAX_BYTES` | 8388608 | tope del archivo |
