# RadarPrice API

## Arranque
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://127.0.0.1:8000/docs
```
La BD `radarprice.db` se crea y siembra al arrancar. Re-sembrar: `python -m app.seed.seed --reset`.
Tests: `pip install -r requirements-dev.txt && pytest`.

## Despliegue (Docker)
Imagen de producción: usuario sin privilegios, 1 worker, healthcheck en `/health`,
SQLite en el volumen `/data` y `RADARPRICE_DEMO_DATA=false` por defecto.
```bash
docker build -t radarprice-api .
docker run -d -p 8000:8000 -v radarprice-data:/data \
  -e RADARPRICE_SYNC_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  -e RADARPRICE_SYNC_INTERVAL_MINUTES=360 \
  radarprice-api
```
Vale para cualquier servicio con Docker (Render, Fly.io, Railway, VPS): exponer el puerto `$PORT`
(8000 por defecto) detrás de HTTPS y montar un volumen persistente en `/data`.
Al arrancar se registra la configuración efectiva (`demo_data`, scrapers reales, sync) en los logs.

## Render (plan gratuito)
`render.yaml` define la API (Docker, Frankfurt) y un PostgreSQL gratuito conectado por
`RADARPRICE_DATABASE_URL` (la URL `postgres://…` de Render se adapta sola a asyncpg).

1. Render → **New → Blueprint** → elegir este repositorio → **Apply**. Crea `radarprice-api` y `radarprice-db`.
2. Cuando el deploy termine, abrir `https://<servicio>.onrender.com/health` → `{"status":"ok"}`.
   Los logs muestran la línea `arranque: demo_data=…` con la configuración efectiva.
3. En el servicio → **Environment**, copiar el valor generado de `RADARPRICE_SYNC_API_KEY`.
4. En GitHub (este repo) → **Settings → Secrets and variables → Actions**, crear:
   `RADARPRICE_API_URL` (`https://<servicio>.onrender.com`) y `RADARPRICE_SYNC_API_KEY`.
5. **Actions → Sync programado → Run workflow** para el primer sync; después corre cada 6 h.

Limitaciones del plan gratuito (confírmalas en la web de Render, cambian con el tiempo):
el servicio se duerme sin tráfico y el primer acceso tarda ~1 min; no hay sync dentro del
proceso (lo lanza GitHub Actions); el PostgreSQL gratuito tiene fecha de caducidad y cupo de
almacenamiento. `RADARPRICE_DEMO_DATA=true` mientras solo haya un scraper real.

| Método | Ruta | Notas |
|---|---|---|
| GET | `/api/v1/products/deals?size=42.5&limit=20` | ranking por `savingsPercent` |
| GET | `/api/v1/products?q=panda&size=43&limit=200&offset=0` | catálogo/búsqueda paginada por SKU (la app carga el catálogo aquí) |
| GET | `/api/v1/products/{sku}` | 404 `{detail, sku}` |
| GET | `/api/v1/products/{sku}/history?days=30` | `days+1` puntos `{date: "YYYY-MM-DD", price}` |
| POST | `/api/v1/sync` | exige `X-API-Key` = `RADARPRICE_SYNC_API_KEY` (sin clave: 503); 409 si hay otro en curso |
| GET | `/api/v1/alerts` | alertas del dispositivo (`X-Device-Id`), con `currentPrice` y `triggeredAt` |
| POST | `/api/v1/alerts` | `{sku, targetPrice, targetSize?}` → 201; 409 duplicada (misma zapatilla y talla) o >50 |
| PATCH | `/api/v1/alerts/{id}` | `{isActive?, targetPrice?}`; 404 si no es de este dispositivo |
| DELETE | `/api/v1/alerts/{id}` | 204 |

## Datos reales vs demo
| | `RADARPRICE_DEMO_DATA=true` (dev) | `RADARPRICE_DEMO_DATA=false` (producción) |
|---|---|---|
| Ofertas servidas | reales + simuladas (`source: "simulated"`) | solo `source: "live"` |
| Histórico | sintético sembrado + syncs | solo puntos de syncs reales |
| Tiendas en el sync | reales + simuladas | solo `RADARPRICE_REAL_SCRAPERS` |

La app marca como "Estimado" cualquier oferta `simulated`.
Sync automático: `RADARPRICE_SYNC_INTERVAL_MINUTES=360` (tarea en el proceso; con varios workers usar cron → `POST /api/v1/sync`).

## Alertas
Sin cuentas: la app genera un `X-Device-Id` aleatorio (128 bits) y lo guarda en el dispositivo.
Cada sync evalúa las alertas activas con los precios visibles (solo `live` si `DEMO_DATA=false`):
al cruzar el objetivo se rellena `triggeredAt` (una vez) y se limpia si el precio vuelve a subir.
`alertsTriggered` en el informe del sync = avisos nuevos. Push (FCM/APNs) pendiente de credenciales:
el punto de enganche está en `SyncService._persist`.

## Catálogo
11 zapatillas (`app/seed/catalog.py`), tallas EU 36–46 según segmento (`gender`: `men` | `women` | `unisex`)
y ofertas deterministas en Nike/adidas, Zalando, Foot Locker y StockX.
Con una BD previa, el arranque añade columnas nuevas y los SKUs que falten sin borrar datos.

## Flutter
JSON camelCase = `Product.fromJson` / `PricePoint.fromJson`. Campos extra: `colorway`, `gender`, `savingsPercent`.
Emulador Android: `http://10.0.2.2:8000` (y `android:usesCleartextTraffic="true"` en debug).

## Scrapers
`build_default_scrapers()` (app/scrapers/registry.py): tiendas de `RADARPRICE_REAL_SCRAPERS` con scraper real
(`SCRAPER_REGISTRY`), el resto simuladas deterministas por día.

| Tienda | Módulo | Por defecto | Fuente |
|---|---|---|---|
| Nike | `app/scrapers/nike.py` | activo | `__NEXT_DATA__` de la PDP |
| Zalando | `app/scrapers/zalando.py` | opt-in: `RADARPRICE_REAL_SCRAPERS='["Nike","Zalando"]'` | JSON-LD `Product` / JSON embebido |

Fallo de una tienda (bloqueo, red, HTML desconocido) → error en el informe del sync; sus ofertas previas se conservan.
Prueba en vivo: `python -m app.scrapers.zalando HQ8708`. Tienda nueva: subclase de `HttpScraper` y alta en `SCRAPER_REGISTRY`.
