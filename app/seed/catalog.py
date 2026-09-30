"""Catálogo semilla (EUR, mercado ES): 11 zapatillas populares, tallas EU 36–46.

Ofertas deterministas por SKU × tienda (sin aleatoriedad entre ejecuciones):
- Tienda oficial (Nike / adidas): precio retail o rebaja fija; stock irregular.
- Zalando / Foot Locker: descuento en un rango propio del modelo (.95 / .99).
- StockX: reventa con prima del modelo; casi siempre hay stock (precio entero).
Algunas tiendas no listan todas las tallas: la app las muestra como no disponibles.
"""
import hashlib
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from types import MappingProxyType
from typing import Final, Literal

Quote = tuple[Decimal, bool]
_PriceFn = Callable[[random.Random], Decimal]
Gender = Literal["men", "women", "unisex"]
# CDN propio previsto al inicio; nunca existió. Las filas antiguas que lo usan se corrigen al arrancar.
LEGACY_IMAGE_CDN: Final = "https://cdn.radarprice.app/"

# Foto inicial verificada en vivo (el SKU coincide en la tienda de origen) para modelos que
# ninguna tienda activa aporta. El resto arranca sin foto ("" → monograma en la app) y el
# sync pone la de la tienda real (la oficial primero). Verificadas: sonda de sep. 2026.
FALLBACK_IMAGES: Final[Mapping[str, str]] = MappingProxyType({
    # New Balance (CDN oficial Scene7).
    "BB550WT1": "https://nb.scene7.com/is/image/NB/bb550wt1_nb_02_i?$pdpflexf2$&wid=800&hei=800",
    # Asphaltgold (Shopify): limita por IP al servidor (429 intermitente); foto fija de respaldo.
    "BD7633": "https://cdn.shopify.com/s/files/1/0473/6965/0340/files/"
              "9d7763e698666f7e8c7c44e59e6ad80cc05998f8_BD7633_Adidas_Handball_Spezial_Collegiate_Navy_Clear_Sky_Gum"
              "_5_os_1_cc5183cb-0487-4465-911e-5173074d4557.jpg?v=1732515529&width=800",
    # afew-store.com (Shopify): ni Nike ES ni las tiendas activas venden ya estos modelos.
    "DH6927-111": "https://cdn.shopify.com/s/files/1/2718/4356/files/"
                  "air-jordan-4-retro-white-black-neutral-grey-dh6927-111-footwear_20_3E_20sneaker.jpg"
                  "?v=1763453595&width=800",
    "DD1503-101": "https://cdn.shopify.com/s/files/1/2718/4356/files/"
                  "nike-wmns-dunk-low-white-black-white-dd1503-101-footwear_20_3E_20sneaker.jpg"
                  "?v=1715609267&width=800",
})

MEN_SIZES: Final = ("39", "40", "40.5", "41", "42", "42.5", "43", "44", "44.5", "45", "46")
WOMEN_SIZES: Final = ("36", "36.5", "37.5", "38", "38.5", "39", "40", "40.5", "41", "42")
UNISEX_SIZES: Final = (
    "36", "36.5", "37.5", "38", "38.5", "39", "40", "40.5", "41", "42", "42.5", "43", "44", "44.5", "45", "46",
)
_SIZES_BY_GENDER: Final[Mapping[Gender, tuple[str, ...]]] = MappingProxyType({
    "men": MEN_SIZES, "women": WOMEN_SIZES, "unisex": UNISEX_SIZES,
})


@dataclass(frozen=True, slots=True)
class SeedProduct:
    sku: str
    brand: str
    name: str
    colorway: str
    gender: Gender
    retail_price: Decimal
    offers: Mapping[str, Mapping[str, Quote]]  # tienda → talla → (precio, stock)

    @property
    def image_url(self) -> str:
        return FALLBACK_IMAGES.get(self.sku, "")


@dataclass(frozen=True, slots=True)
class _Market:
    """Perfil de precios de un modelo. Rangos = factor sobre retail."""
    official: str | None = None
    official_sale: float = 0.0
    zalando: tuple[float, float] | None = None
    foot_locker: tuple[float, float] | None = None
    stockx_premium: float = 1.0


def _rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _price(value: float, cents: str) -> Decimal:
    """Precio comercial: euros enteros + terminación fija (.99, .95, .00)."""
    euros = Decimal(str(value)).to_integral_value(rounding=ROUND_FLOOR)
    return max(euros + Decimal(f"0.{cents}"), Decimal("1.00"))


def _store_quotes(
    sku: str, store: str, sizes: tuple[str, ...], *,
    price_for: _PriceFn, stock_rate: float, listed_rate: float = 0.92,
) -> Mapping[str, Quote]:
    rng = _rng(sku, store)
    quotes: dict[str, Quote] = {}
    for size in sizes:
        listed = rng.random() < listed_rate
        in_stock = rng.random() < stock_rate
        price = price_for(rng)
        if listed:
            quotes[size] = (price, in_stock)
    return MappingProxyType(quotes)


def _product(
    sku: str, brand: str, name: str, colorway: str, gender: Gender, retail: str, market: _Market,
) -> SeedProduct:
    retail_price = Decimal(retail)
    base = float(retail_price)
    sizes = _SIZES_BY_GENDER[gender]
    offers: dict[str, Mapping[str, Quote]] = {}

    if market.official:
        official_price = (
            retail_price if market.official_sale == 0 else _price(base * (1 - market.official_sale), "99")
        )
        offers[market.official] = _store_quotes(
            sku, market.official, sizes, price_for=lambda _: official_price, stock_rate=0.55,
        )
    for store, band, cents in (("Zalando", market.zalando, "95"), ("Foot Locker", market.foot_locker, "99")):
        if band is not None:
            lo, hi = band
            offers[store] = _store_quotes(
                sku, store, sizes, price_for=lambda r, lo=lo, hi=hi, cents=cents: _price(base * r.uniform(lo, hi), cents),
                stock_rate=0.7,
            )
    premium = market.stockx_premium
    offers["StockX"] = _store_quotes(
        sku, "StockX", sizes, price_for=lambda r: _price(base * premium * r.uniform(0.92, 1.12), "00"),
        stock_rate=0.95, listed_rate=1.0,
    )

    return SeedProduct(
        sku=sku, brand=brand, name=name, colorway=colorway, gender=gender,
        retail_price=retail_price, offers=MappingProxyType(offers),
    )


SEED_PRODUCTS: Final[tuple[SeedProduct, ...]] = (
    _product("DH6927-111", "Jordan", 'Air Jordan 4 Retro "Military Black"', "White/Black-Neutral Grey", "men",
             "210.00", _Market(official="Nike", foot_locker=(1.0, 1.05), stockx_premium=1.12)),
    _product("HQ8708", "adidas", 'Campus 00s "Core Black"', "Core Black/Cloud White/Off White", "unisex",
             "120.00", _Market(official="adidas", zalando=(0.72, 0.82), foot_locker=(0.8, 0.92), stockx_premium=0.86)),
    _product("HF5441-100", "Nike", 'Dunk Low Retro "White Black" (Panda)', "White/Black", "men",
             "119.99", _Market(official="Nike", zalando=(0.85, 0.95), foot_locker=(0.83, 0.95), stockx_premium=0.82)),
    _product("DD1503-101", "Nike", 'Dunk Low "White Black" (W)', "White/Black-White", "women",
             "119.99", _Market(official="Nike", zalando=(0.8, 0.92), foot_locker=(0.85, 1.0), stockx_premium=0.9)),
    _product("CW2288-111", "Nike", "Air Force 1 '07 \"Triple White\"", "White/White", "men",
             "119.99", _Market(official="Nike", zalando=(0.82, 0.95), foot_locker=(0.9, 1.0), stockx_premium=0.95)),
    _product("DD8959-100", "Nike", "Air Force 1 '07 \"Triple White\" (W)", "White/White-White-White", "women",
             "119.99", _Market(official="Nike", official_sale=0.2, zalando=(0.8, 0.9), foot_locker=(0.85, 0.95),
                               stockx_premium=0.92)),
    _product("B75806", "adidas", 'Samba OG "Cloud White"', "Cloud White/Core Black/Clear Granite", "unisex",
             "120.00", _Market(official="adidas", zalando=(0.85, 1.0), foot_locker=(0.9, 1.0), stockx_premium=1.05)),
    _product("BD7633", "adidas", 'Handball Spezial "Collegiate Navy"', "Collegiate Navy/Clear Sky/Gum", "unisex",
             "110.00", _Market(official="adidas", official_sale=0.3, zalando=(0.72, 0.85), foot_locker=(0.8, 0.95),
                               stockx_premium=0.95)),
    _product("BB550WT1", "New Balance", '550 "White Green"', "White/Green", "unisex",
             "130.00", _Market(zalando=(0.7, 0.8), foot_locker=(0.75, 0.9), stockx_premium=0.8)),
    _product("CN8490-100", "Nike", 'Air Max 90 "Triple White"', "White/White-Wolf Grey", "men",
             "149.99", _Market(official="Nike", official_sale=0.25, zalando=(0.7, 0.85), foot_locker=(0.75, 0.9),
                               stockx_premium=0.7)),
    _product("604133-050", "Nike", 'Air Max Plus "Triple Black"', "Black/Black-Black", "men",
             "189.99", _Market(official="Nike", official_sale=0.2, zalando=(0.75, 0.9), foot_locker=(0.8, 0.95),
                               stockx_premium=0.8)),
)

SEED_BY_SKU: Final[Mapping[str, SeedProduct]] = MappingProxyType({p.sku: p for p in SEED_PRODUCTS})
