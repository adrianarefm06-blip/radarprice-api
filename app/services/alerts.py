"""Evaluación de alertas (reglas puras + barrido tras cada sync).

  precio actual = oferta en stock más barata (en la talla, si la alerta la fija),
                  solo `live` cuando demo_data=false
  activa ∧ actual ≤ objetivo ∧ sin disparar → triggered_at/price = ahora/actual  (evento nuevo)
  disparada ∧ (actual > objetivo ∨ sin stock) → se limpia (se rearma)
  pausada → no cambia
"""
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SOURCE_LIVE, PriceAlert, StoreOffer
from app.services.pricing import lowest_in_stock


def visible_offers(offers: Iterable[StoreOffer], *, live_only: bool) -> list[StoreOffer]:
    return [o for o in offers if not live_only or o.source == SOURCE_LIVE]


def current_price(offers: Iterable[StoreOffer], size: str | None, *, live_only: bool) -> Decimal | None:
    return lowest_in_stock(visible_offers(offers, live_only=live_only), size)


def apply_evaluation(alert: PriceAlert, current: Decimal | None, now: datetime) -> bool:
    """Actualiza el estado de disparo. True = disparo nuevo (hay que avisar)."""
    if not alert.is_active:
        return False
    hit = current is not None and current <= alert.target_price
    if hit and alert.triggered_at is None:
        alert.triggered_at, alert.triggered_price = now, current
        return True
    if not hit and alert.triggered_at is not None:
        alert.triggered_at = alert.triggered_price = None
    return False


async def evaluate_alerts(
    session: AsyncSession,
    offers_by_sku: Mapping[str, Sequence[StoreOffer]],
    now: datetime,
    *,
    live_only: bool,
) -> list[PriceAlert]:
    """Evalúa todas las alertas activas. Devuelve las que se acaban de disparar."""
    fired: list[PriceAlert] = []
    for alert in await session.scalars(select(PriceAlert).where(PriceAlert.is_active.is_(True))):
        offers = offers_by_sku.get(alert.product_sku, ())
        if apply_evaluation(alert, current_price(offers, alert.target_size, live_only=live_only), now):
            fired.append(alert)
    return fired
