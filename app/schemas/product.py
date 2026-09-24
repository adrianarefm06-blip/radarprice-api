"""Esquemas de salida: espejo exacto de los modelos Flutter (camelCase)."""
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def _assume_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# SQLite devuelve fechas sin zona aunque se guarden en UTC: sin esto, el cliente
# las interpretaría como hora local.
UtcDatetime = Annotated[datetime, AfterValidator(_assume_utc)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class StoreOfferOut(CamelModel):
    store_name: str
    store_logo_url: str
    price: float = Field(gt=0)
    original_price: float | None = None  # extra: precio tachado de la tienda
    in_stock: bool
    affiliate_url: str
    source: Literal["live", "simulated"]  # simulated = dato de demostración
    last_updated: UtcDatetime  # extra: Flutter lo ignora


class ProductOut(CamelModel):
    id: str
    sku: str
    brand: str
    model: str
    image_url: str
    colorway: str | None
    gender: Literal["men", "women", "unisex"]
    lowest_price: float
    retail_price: float
    size_offers: dict[str, list[StoreOfferOut]]
    # extra: % ahorro vs retail (para la talla pedida si aplica). Negativo = reventa.
    savings_percent: float


class PricePointOut(CamelModel):
    date: date  # serializa "YYYY-MM-DD"
    price: float
