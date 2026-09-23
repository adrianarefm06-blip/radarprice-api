from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.deps import ProductRepoDep, SettingsDep
from app.core.config import Settings
from app.core.errors import ProductNotFoundError
from app.schemas.product import PricePointOut, ProductOut
from app.services.catalog import to_product_out
from app.services.pricing import lowest_in_stock

router = APIRouter(prefix="/products", tags=["products"])

SizeQuery = Annotated[
    str | None,
    Query(pattern=r"^\d{2}(\.5)?$", description="Talla EU, p. ej. 42 o 42.5", examples=["42.5"]),
]
SkuPath = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9\-]+$")]


def _validate_size(size: str | None, settings: Settings) -> None:
    if size is not None and size not in settings.supported_sizes:
        raise HTTPException(
            422,  # constante renombrada entre versiones de Starlette
            f"Talla no soportada: {size}. Válidas: {', '.join(settings.supported_sizes)}",
        )


@router.get("/deals", response_model=list[ProductOut], summary="Feed de chollos")
async def get_deals(
    repo: ProductRepoDep,
    settings: SettingsDep,
    size: SizeQuery = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ProductOut]:
    """Ordenados por % de ahorro vs retail. Con `size`: solo stock en esa talla y ranking por esa talla."""
    _validate_size(size, settings)
    products = await repo.list_with_offers()
    if size is not None:
        products = [p for p in products if lowest_in_stock(p.offers, size) is not None]
    ranked = sorted((to_product_out(p, size=size) for p in products), key=lambda p: p.savings_percent, reverse=True)
    return ranked[:limit]


@router.get("", response_model=list[ProductOut], summary="Búsqueda por marca, modelo o SKU")
async def search_products(
    repo: ProductRepoDep,
    settings: SettingsDep,
    q: Annotated[str, Query(max_length=80)] = "",
    size: SizeQuery = None,
) -> list[ProductOut]:
    _validate_size(size, settings)
    products = await repo.list_with_offers(q.strip() or None)
    if size is not None:
        products = [p for p in products if lowest_in_stock(p.offers, size) is not None]
    return [to_product_out(p, size=size) for p in products]


@router.get("/{sku}", response_model=ProductOut, summary="Ficha con ofertas por talla")
async def get_product(sku: SkuPath, repo: ProductRepoDep) -> ProductOut:
    product = await repo.get_with_offers(sku.upper())
    if product is None:
        raise ProductNotFoundError(sku)
    return to_product_out(product)


@router.get("/{sku}/history", response_model=list[PricePointOut], summary="Histórico diario de precio mínimo")
async def get_history(
    sku: SkuPath,
    repo: ProductRepoDep,
    days: Annotated[int, Query(ge=1, le=365, description="Ventana en días (30 o 90 en la app)")] = 90,
) -> list[PricePointOut]:
    normalized = sku.upper()
    if not await repo.exists(normalized):
        raise ProductNotFoundError(sku)
    points = await repo.history_since(normalized, date.today() - timedelta(days=days))
    return [PricePointOut(date=p.date, price=float(p.price)) for p in points]
