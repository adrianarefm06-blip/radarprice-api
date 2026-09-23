"""Catálogo semilla (EUR, mercado ES). Mismos datos que el mock de Flutter."""
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

Quote = tuple[Decimal, bool]
_IMAGE_CDN: Final = "https://cdn.radarprice.app/products"


@dataclass(frozen=True, slots=True)
class SeedProduct:
    sku: str
    brand: str
    name: str
    retail_price: Decimal
    offers: Mapping[str, Mapping[str, Quote]]  # tienda → talla → (precio, stock)

    @property
    def image_url(self) -> str:
        return f"{_IMAGE_CDN}/{self.sku.lower()}.webp"


def _q(price: str, in_stock: bool) -> Quote:
    return Decimal(price), in_stock


def _sizes(*quotes: Quote) -> Mapping[str, Quote]:
    return MappingProxyType(dict(zip(("41", "42", "42.5", "43", "44"), quotes, strict=True)))


SEED_PRODUCTS: Final[tuple[SeedProduct, ...]] = (
    SeedProduct(
        sku="DH6927-111",
        brand="Jordan",
        name='Air Jordan 4 Retro "Military Black"',
        retail_price=Decimal("210.00"),
        offers=MappingProxyType({
            "Nike": _sizes(_q("210.00", False), _q("210.00", False), _q("210.00", False), _q("210.00", False), _q("210.00", True)),
            "Foot Locker": _sizes(_q("219.99", True), _q("219.99", False), _q("219.99", True), _q("219.99", True), _q("219.99", False)),
            "StockX": _sizes(_q("268.00", True), _q("245.00", True), _q("239.00", True), _q("252.00", True), _q("231.00", True)),
        }),
    ),
    SeedProduct(
        sku="HQ8708",
        brand="adidas",
        name='Campus 00s "Core Black"',
        retail_price=Decimal("120.00"),
        offers=MappingProxyType({
            "adidas": _sizes(_q("120.00", True), _q("120.00", False), _q("120.00", True), _q("120.00", True), _q("120.00", False)),
            "Foot Locker": _sizes(_q("99.99", True), _q("99.99", True), _q("109.99", True), _q("99.99", False), _q("109.99", True)),
            "Zalando": _sizes(_q("89.95", False), _q("94.95", True), _q("94.95", True), _q("89.95", True), _q("99.95", True)),
            "StockX": _sizes(_q("104.00", True), _q("98.00", True), _q("112.00", True), _q("101.00", True), _q("96.00", True)),
        }),
    ),
    SeedProduct(
        sku="HF5441-100",
        brand="Nike",
        name='Dunk Low Retro "White Black" (Panda)',
        retail_price=Decimal("119.99"),
        offers=MappingProxyType({
            "Nike": _sizes(_q("119.99", True), _q("119.99", False), _q("119.99", False), _q("119.99", True), _q("119.99", False)),
            "Foot Locker": _sizes(_q("109.99", True), _q("109.99", True), _q("119.99", True), _q("99.99", True), _q("119.99", False)),
            "StockX": _sizes(_q("96.00", True), _q("102.00", True), _q("108.00", True), _q("99.00", True), _q("93.00", True)),
        }),
    ),
)

SEED_BY_SKU: Final[Mapping[str, SeedProduct]] = MappingProxyType({p.sku: p for p in SEED_PRODUCTS})
