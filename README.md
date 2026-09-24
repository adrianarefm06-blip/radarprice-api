# RadarPrice API

## Arranque
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://127.0.0.1:8000/docs
```
La BD `radarprice.db` se crea y siembra al arrancar. Re-sembrar: `python -m app.seed.seed --reset`.
Tests: `pip install -r requirements-dev.txt && pytest`.

## Endpoints
| Método | Ruta | Notas |
|---|---|---|
| GET | `/api/v1/products/deals?size=42.5&limit=20` | ranking por `savingsPercent` |
| GET | `/api/v1/products?q=panda&size=43` | búsqueda (contrato `searchProducts`) |
| GET | `/api/v1/products/{sku}` | 404 `{detail, sku}` |
| GET | `/api/v1/products/{sku}/history?days=30` | `days+1` puntos `{date: "YYYY-MM-DD", price}` |
| POST | `/api/v1/sync` | `X-API-Key` si `RADARPRICE_SYNC_API_KEY`; 409 si hay otro en curso |

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
