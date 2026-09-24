from pydantic import Field

from app.schemas.product import CamelModel, UtcDatetime

_SIZE_PATTERN = r"^\d{2}(\.5)?$"


class AlertCreateIn(CamelModel):
    sku: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9\-]+$")
    target_price: float = Field(gt=0, le=10_000)
    target_size: str | None = Field(default=None, pattern=_SIZE_PATTERN)


class AlertUpdateIn(CamelModel):
    is_active: bool | None = None
    target_price: float | None = Field(default=None, gt=0, le=10_000)


class AlertOut(CamelModel):
    id: str
    product_id: str
    sku: str
    target_price: float
    target_size: str | None
    is_active: bool
    created_at: UtcDatetime
    # Último cruce del objetivo (se limpia al volver a subir el precio).
    triggered_at: UtcDatetime | None
    triggered_price: float | None
    # Precio actual con los datos visibles (en la talla si se fijó). None = sin stock.
    current_price: float | None
