from datetime import datetime

from app.schemas.product import CamelModel


class SyncErrorOut(CamelModel):
    store_name: str
    sku: str
    message: str


class SyncReportOut(CamelModel):
    started_at: datetime
    finished_at: datetime
    products_processed: int
    offers_upserted: int
    offers_marked_out_of_stock: int
    history_points_upserted: int
    errors: list[SyncErrorOut]
