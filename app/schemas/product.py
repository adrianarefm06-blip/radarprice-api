"""Esquemas de salida: espejo exacto de los modelos Flutter (camelCase)."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class StoreOfferOut(CamelModel):
    store_name: str
    store_logo_url: str
    price: float = Field(gt=0)
    in_stock: bool
    affiliate_url: str
    last_updated: datetime  # extra: Flutter lo ignora


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
