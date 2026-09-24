from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy.orm.interfaces import LoaderOption

from app.db.models import SOURCE_LIVE, PriceHistory, Product, StoreOffer


class ProductRepository:
    """[live_only] = sin datos de demostración: solo ofertas e histórico con source=live."""

    def __init__(self, session: AsyncSession, *, live_only: bool = False) -> None:
        self._session = session
        self._live_only = live_only

    def _offers_loader(self) -> LoaderOption:
        if self._live_only:
            return selectinload(Product.offers.and_(StoreOffer.source == SOURCE_LIVE))
        return selectinload(Product.offers)

    async def list_with_offers(self, query: str | None = None) -> Sequence[Product]:
        stmt = select(Product).options(self._offers_loader()).order_by(Product.sku)
        for token in (query or "").lower().replace('"', " ").split():
            like = f"%{token}%"
            stmt = stmt.where(or_(
                func.lower(Product.brand).like(like),
                func.lower(Product.name).like(like),
                func.lower(Product.sku).like(like),
            ))
        return (await self._session.scalars(stmt)).all()

    async def get_with_offers(self, sku: str) -> Product | None:
        stmt = select(Product).options(self._offers_loader()).where(Product.sku == sku)
        return (await self._session.scalars(stmt)).one_or_none()

    async def exists(self, sku: str) -> bool:
        return (await self._session.scalar(select(func.count()).where(Product.sku == sku))) == 1

    async def history_since(self, sku: str, since: date) -> Sequence[PriceHistory]:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.product_sku == sku, PriceHistory.date >= since)
            .order_by(PriceHistory.date)
        )
        if self._live_only:
            stmt = stmt.where(PriceHistory.source == SOURCE_LIVE)
        return (await self._session.scalars(stmt)).all()
