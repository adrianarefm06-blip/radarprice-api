from collections import defaultdict

from app.core.stores import store_logo_url
from app.db.models import Product, StoreOffer
from app.schemas.product import ProductOut, StoreOfferOut
from app.services.pricing import lowest_in_stock, savings_percent, size_sort_key

_GENDERS = frozenset({"men", "women", "unisex"})


def product_id(sku: str) -> str:
    return f"prd_{sku.lower()}"


def _offer_out(offer: StoreOffer) -> StoreOfferOut:
    return StoreOfferOut(
        store_name=offer.store_name,
        store_logo_url=store_logo_url(offer.store_name),
        price=float(offer.price),
        original_price=float(offer.original_price) if offer.original_price is not None else None,
        in_stock=offer.in_stock,
        affiliate_url=offer.affiliate_url,
        last_updated=offer.last_updated,
    )


def to_product_out(product: Product, *, size: str | None = None) -> ProductOut:
    """Requiere `product.offers` cargado. Sin stock → lowestPrice = retail (contrato Flutter)."""
    grouped: dict[str, list[StoreOffer]] = defaultdict(list)
    for offer in product.offers:
        grouped[offer.size].append(offer)

    size_offers = {
        s: [_offer_out(o) for o in sorted(grouped[s], key=lambda o: (not o.in_stock, o.price))]
        for s in sorted(grouped, key=size_sort_key)
    }
    lowest = lowest_in_stock(product.offers) or product.retail_price
    reference = lowest_in_stock(product.offers, size) if size else lowest

    return ProductOut(
        id=product_id(product.sku),
        sku=product.sku,
        brand=product.brand,
        model=product.name,
        image_url=product.image_url,
        colorway=product.colorway,
        gender=product.gender if product.gender in _GENDERS else "unisex",
        lowest_price=float(lowest),
        retail_price=float(product.retail_price),
        size_offers=size_offers,
        savings_percent=savings_percent(product.retail_price, reference) if reference is not None else 0.0,
    )
