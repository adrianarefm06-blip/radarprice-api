"""Helpers de parsing compartidos por los scrapers reales (funciones puras)."""
from decimal import Decimal, InvalidOperation
from typing import Any


def to_price(value: Any) -> Decimal | None:
    """89.95 / '89,95 €' / '1.089,95' → Decimal(2 decimales). Inválido o <= 0 → None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, Decimal):
            price = value
        elif isinstance(value, (int, float)):
            price = Decimal(str(value))
        elif isinstance(value, str):
            text = value.replace("€", "").replace("EUR", "").replace("\xa0", "").strip()
            if "," in text:  # formato es-ES: '.' miles, ',' decimales
                text = text.replace(".", "").replace(",", ".")
            price = Decimal(text)
        else:
            return None
    except InvalidOperation:
        return None
    return price.quantize(Decimal("0.01")) if price > 0 else None


def normalize_size(raw: str) -> str:
    """'42,5' → '42.5' · '42.0' → '42'."""
    value = raw.replace(",", ".")
    return value[:-2] if value.endswith(".0") else value
