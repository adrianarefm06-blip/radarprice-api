"""Tienda simulada: parte del catálogo semilla y aplica variación diaria determinista.

Misma fecha + SKU + tienda ⇒ mismo resultado (sync idempotente dentro del día).
Tiendas oficiales mantienen precio retail; solo varía el stock.
"""
import random
from collections.abc import Callable, Sequence
from datetime import date
from decimal import ROUND_FLOOR, Decimal

import httpx

from app.core.stores import STORES
from app.scrapers.base import BaseScraper, ProductRef, ScrapedOffer
from app.seed.catalog import SEED_BY_SKU

_STOCK_FLIP_PROBABILITY = 0.10


class SimulatedStoreScraper(BaseScraper):
    def __init__(
        self,
        store_name: str,
        *,
        volatility: float = 0.04,
        clock: Callable[[], date] = date.today,
    ) -> None:
        if store_name not in STORES:
            raise ValueError(f"Tienda desconocida: {store_name}")
        if not 0 <= volatility < 0.5:
            raise ValueError("volatility debe estar en [0, 0.5)")
        self._store = STORES[store_name]
        self._volatility = 0.0 if self._store.is_official_retailer else volatility
        self._clock = clock

    @property
    def is_live(self) -> bool:
        return False

    @property
    def store_name(self) -> str:
        return self._store.name

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        seed = SEED_BY_SKU.get(product.sku)
        baseline = seed.offers.get(self.store_name) if seed else None
        if not baseline:
            return []

        rng = random.Random(f"{self._clock().isoformat()}|{product.sku}|{self.store_name}")
        url = self._store.affiliate_url(product.sku)
        offers: list[ScrapedOffer] = []
        for size, (base_price, base_stock) in baseline.items():
            factor = Decimal(str(1 + rng.uniform(-self._volatility, self._volatility)))
            in_stock = (not base_stock) if rng.random() < _STOCK_FLIP_PROBABILITY else base_stock
            offers.append(ScrapedOffer(
                size=size,
                price=_round_like(base_price * factor, base_price),
                in_stock=in_stock,
                affiliate_url=url,
            ))
        return offers


def _round_like(value: Decimal, reference: Decimal) -> Decimal:
    """Conserva la terminación comercial del precio base (.99, .95 o entero)."""
    cents = reference - reference.to_integral_value(rounding=ROUND_FLOOR)
    euros = value.to_integral_value(rounding=ROUND_FLOOR)
    return max(euros + cents, Decimal("1.00")).quantize(Decimal("0.01"))
