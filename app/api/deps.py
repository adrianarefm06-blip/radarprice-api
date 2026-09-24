import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories.products import ProductRepository
from app.services.sync_service import SyncService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_product_repository(session: Annotated[AsyncSession, Depends(get_session)]) -> ProductRepository:
    return ProductRepository(session)


def get_sync_service(request: Request) -> SyncService:
    return request.app.state.sync_service


SettingsDep = Annotated[Settings, Depends(get_settings)]
ProductRepoDep = Annotated[ProductRepository, Depends(get_product_repository)]
SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]


def require_sync_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.sync_api_key
    if not expected:
        # Cerrado por defecto: un sync dispara peticiones a tiendas de terceros.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Sync deshabilitado: define RADARPRICE_SYNC_API_KEY para activarlo",
        )
    if not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key inválida o ausente")
