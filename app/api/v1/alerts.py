"""Alertas de precio por dispositivo.

Sin cuentas de usuario: la app envía `X-Device-Id` (128 bits aleatorios generados en el
dispositivo). Funciona como un token al portador: quien lo conozca ve esas alertas, así
que la app nunca lo muestra ni lo comparte.
"""
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SettingsDep, get_session
from app.core.config import Settings
from app.core.errors import ProductNotFoundError
from app.db.models import PriceAlert, Product, StoreOffer
from app.repositories.alerts import AlertRepository, new_alert_id
from app.schemas.alert import AlertCreateIn, AlertOut, AlertUpdateIn
from app.services.alerts import apply_evaluation, current_price
from app.services.catalog import product_id

router = APIRouter(prefix="/alerts", tags=["alerts"])

MAX_ALERTS_PER_DEVICE: Final = 50
_DEVICE_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{16,64}$"

DeviceIdHeader = Annotated[
    str,
    Header(alias="X-Device-Id", pattern=_DEVICE_ID_PATTERN, description="Id aleatorio del dispositivo"),
]
AlertIdPath = Annotated[str, Path(pattern=r"^alt_[0-9a-f]{24}$")]


def get_alert_repository(
    device_id: DeviceIdHeader, session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertRepository:
    return AlertRepository(session, device_id)


AlertRepoDep = Annotated[AlertRepository, Depends(get_alert_repository)]


def _live_only(settings: Settings) -> bool:
    return not settings.demo_data


async def _offers_by_sku(session: AsyncSession, skus: set[str]) -> dict[str, list[StoreOffer]]:
    grouped: dict[str, list[StoreOffer]] = defaultdict(list)
    if skus:
        for offer in await session.scalars(select(StoreOffer).where(StoreOffer.product_sku.in_(skus))):
            grouped[offer.product_sku].append(offer)
    return grouped


def _to_out(alert: PriceAlert, current: Decimal | None) -> AlertOut:
    return AlertOut(
        id=alert.id,
        product_id=product_id(alert.product_sku),
        sku=alert.product_sku,
        target_price=float(alert.target_price),
        target_size=alert.target_size,
        is_active=alert.is_active,
        created_at=alert.created_at,
        triggered_at=alert.triggered_at,
        triggered_price=float(alert.triggered_price) if alert.triggered_price is not None else None,
        current_price=float(current) if current is not None else None,
    )


async def _render(repo: AlertRepository, alerts: Sequence[PriceAlert], settings: Settings) -> list[AlertOut]:
    offers = await _offers_by_sku(repo.session, {a.product_sku for a in alerts})
    live_only = _live_only(settings)
    return [
        _to_out(a, current_price(offers.get(a.product_sku, ()), a.target_size, live_only=live_only))
        for a in alerts
    ]


async def _get_or_404(repo: AlertRepository, alert_id: str) -> PriceAlert:
    alert = await repo.get(alert_id)
    if alert is None:  # también si es de otro dispositivo: no se revela su existencia
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alerta no encontrada")
    return alert


@router.get("", response_model=list[AlertOut], summary="Alertas del dispositivo (más recientes primero)")
async def list_alerts(repo: AlertRepoDep, settings: SettingsDep) -> list[AlertOut]:
    return await _render(repo, await repo.list(), settings)


@router.post(
    "",
    response_model=AlertOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear alerta",
    responses={404: {"description": "SKU inexistente"}, 409: {"description": "Duplicada o límite alcanzado"}},
)
async def create_alert(body: AlertCreateIn, repo: AlertRepoDep, settings: SettingsDep) -> AlertOut:
    sku = body.sku.upper()
    if body.target_size is not None and body.target_size not in settings.supported_sizes:
        raise HTTPException(422, f"Talla no soportada: {body.target_size}")  # constante renombrada en Starlette
    if await repo.session.get(Product, sku) is None:
        raise ProductNotFoundError(sku)
    if await repo.find_duplicate(sku, body.target_size) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya tienes una alerta para esta zapatilla y talla")
    if await repo.count() >= MAX_ALERTS_PER_DEVICE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Máximo {MAX_ALERTS_PER_DEVICE} alertas por dispositivo")

    now = datetime.now(UTC)
    alert = PriceAlert(
        id=new_alert_id(),
        product_sku=sku,
        target_price=Decimal(str(body.target_price)).quantize(Decimal("0.01")),
        target_size=body.target_size,
        is_active=True,
        created_at=now,
    )
    offers = (await _offers_by_sku(repo.session, {sku})).get(sku, [])
    current = current_price(offers, alert.target_size, live_only=_live_only(settings))
    apply_evaluation(alert, current, now)  # si ya está por debajo, nace disparada
    repo.add(alert)
    await repo.session.commit()
    return _to_out(alert, current)


@router.patch("/{alert_id}", response_model=AlertOut, summary="Pausar/reactivar o cambiar el precio objetivo")
async def update_alert(
    alert_id: AlertIdPath, body: AlertUpdateIn, repo: AlertRepoDep, settings: SettingsDep,
) -> AlertOut:
    alert = await _get_or_404(repo, alert_id)
    if body.is_active is not None:
        alert.is_active = body.is_active
    if body.target_price is not None:
        alert.target_price = Decimal(str(body.target_price)).quantize(Decimal("0.01"))
    offers = (await _offers_by_sku(repo.session, {alert.product_sku})).get(alert.product_sku, [])
    current = current_price(offers, alert.target_size, live_only=_live_only(settings))
    # Pausa, reactivación u objetivo nuevo: se reevalúa desde cero con el precio de ahora.
    alert.triggered_at = alert.triggered_price = None
    apply_evaluation(alert, current, datetime.now(UTC))
    await repo.session.commit()
    return _to_out(alert, current)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Borrar alerta")
async def delete_alert(alert_id: AlertIdPath, repo: AlertRepoDep) -> Response:
    await repo.delete(await _get_or_404(repo, alert_id))
    await repo.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
