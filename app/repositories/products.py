from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import PriceHistory, Product


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_with_offers(self, query: str | None = None) -> Sequence[Product]:
        stmt = select(Product).options(selectinload(Product.offers)).order_by(Product.sku)
        for token in (query or "").lower().replace('"', " ").split():
            like = f"%{token}%"
            stmt = stmt.where(or_(
                func.lower(Product.brand).like(like),
                func.lower(Product.name).like(like),
                func.lower(Product.sku).like(like),
            ))
        return (await self._session.scalars(stmt)).all()

    async def get_with_offers(self, sku: str) -> Product | None:
        stmt = select(Product).options(selectinload(Product.offers)).where(Product.sku == sku)
        return (await self._session.scalars(stmt)).one_or_none()

    async def exists(self, sku: str) -> bool:
        return (await self._session.scalar(select(func.count()).where(Product.sku == sku))) == 1

    async def history_since(self, sku: str, since: date) -> Sequence[PriceHistory]:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.product_sku == sku, PriceHistory.date >= since)
            .order_by(PriceHistory.date)
        )
        return (await self._session.scalars(stmt)).all()
