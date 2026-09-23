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

## Flutter
JSON camelCase = `Product.fromJson` / `PricePoint.fromJson` sin cambios.
Emulador Android: `http://10.0.2.2:8000` (y `android:usesCleartextTraffic="true"` en debug).

## Scrapers
`build_default_scrapers()` (app/scrapers/registry.py) devuelve tiendas simuladas deterministas por día.
Tienda real: subclase de `HttpScraper` con `build_url()` + `parse()` y registrarla ahí.
