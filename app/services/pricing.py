"""Reglas de precio puras (sin I/O). Idénticas a las del modelo Flutter."""
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol


class _OfferLike(Protocol):
    size: str
    price: Decimal
    in_stock: bool


def lowest_in_stock(offers: Iterable[_OfferLike], size: str | None = None) -> Decimal | None:
    prices = [o.price for o in offers if o.in_stock and (size is None or o.size == size)]
    return min(prices, default=None)


def savings_percent(retail: Decimal, price: Decimal) -> float:
    if retail <= 0:
        return 0.0
    pct = (retail - price) / retail * 100
    return float(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def size_sort_key(size: str) -> tuple[int, float, str]:
    """'42' < '42.5' < '43'; tallas no numéricas al final."""
    try:
        return (0, float(size.replace(",", ".")), size)
    except ValueError:
        return (1, 0.0, size)
