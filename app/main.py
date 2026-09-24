import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import ProductNotFoundError, SyncAlreadyRunningError
from app.db.base import Base
from app.db.schema import add_missing_columns
from app.db.session import create_engine_and_sessionmaker
from app.scrapers import build_default_scrapers
from app.seed.seed import seed_database
from app.services.scheduler import run_periodic_sync
from app.services.sync_service import SyncService


_logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    """Logs de `app.*` a stderr (docker logs). No toca los de uvicorn ni duplica handlers."""
    logger = logging.getLogger("app")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _logger.info(
            "arranque: demo_data=%s, scrapers reales=%s, sync cada %s min, sync manual %s",
            settings.demo_data, list(settings.real_scrapers), settings.sync_interval_minutes or "—",
            "habilitado" if settings.sync_api_key else "deshabilitado (sin RADARPRICE_SYNC_API_KEY)",
        )
        engine, sessionmaker = create_engine_and_sessionmaker(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(add_missing_columns)
        if settings.seed_on_startup:
            await seed_database(sessionmaker, synthetic_history=settings.demo_data)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker
        app.state.sync_service = SyncService(sessionmaker, build_default_scrapers(settings), settings)
        scheduler: asyncio.Task[None] | None = None
        if settings.sync_interval_minutes:
            scheduler = asyncio.create_task(
                run_periodic_sync(app.state.sync_service, timedelta(minutes=settings.sync_interval_minutes)),
                name="radarprice-periodic-sync",
            )
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler
            await engine.dispose()

    app = FastAPI(title="RadarPrice API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(ProductNotFoundError)
    async def _not_found(_: Request, exc: ProductNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc), "sku": exc.sku})

    @app.exception_handler(SyncAlreadyRunningError)
    async def _sync_running(_: Request, exc: SyncAlreadyRunningError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
