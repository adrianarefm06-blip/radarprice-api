import secrets
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceAlert


def new_alert_id() -> str:
    return f"alt_{secrets.token_hex(12)}"


class AlertRepository:
    """Siempre acotado a un dispositivo: nunca devuelve ni modifica alertas ajenas."""

    def __init__(self, session: AsyncSession, device_id: str) -> None:
        self._session = session
        self._device_id = device_id

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def list(self) -> Sequence[PriceAlert]:
        stmt = (
            select(PriceAlert)
            .where(PriceAlert.device_id == self._device_id)
            .order_by(PriceAlert.created_at.desc(), PriceAlert.id)
        )
        return (await self._session.scalars(stmt)).all()

    async def get(self, alert_id: str) -> PriceAlert | None:
        alert = await self._session.get(PriceAlert, alert_id)
        return alert if alert is not None and alert.device_id == self._device_id else None

    async def count(self) -> int:
        stmt = select(func.count()).select_from(PriceAlert).where(PriceAlert.device_id == self._device_id)
        return int(await self._session.scalar(stmt) or 0)

    async def find_duplicate(self, sku: str, size: str | None) -> PriceAlert | None:
        stmt = select(PriceAlert).where(
            PriceAlert.device_id == self._device_id,
            PriceAlert.product_sku == sku,
            PriceAlert.target_size.is_(None) if size is None else PriceAlert.target_size == size,
        )
        return (await self._session.scalars(stmt)).first()

    def add(self, alert: PriceAlert) -> None:
        alert.device_id = self._device_id
        self._session.add(alert)

    async def delete(self, alert: PriceAlert) -> None:
        await self._session.delete(alert)
