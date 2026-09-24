from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
from app.services.sync_service import SyncService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine, sessionmaker = create_engine_and_sessionmaker(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(add_missing_columns)
        if settings.seed_on_startup:
            await seed_database(sessionmaker)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker
        app.state.sync_service = SyncService(sessionmaker, build_default_scrapers(settings), settings)
        try:
            yield
        finally:
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
