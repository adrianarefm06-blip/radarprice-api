"""PostgreSQL (producción en Render). La normalización de URL siempre se prueba; el flujo
completo solo con RADARPRICE_TEST_POSTGRES_URL (en CI hay un servicio Postgres).

  RADARPRICE_TEST_POSTGRES_URL=postgresql://radarprice:secret@localhost:15432/radarprice pytest tests/test_postgres.py
"""
import asyncio
import os
from collections.abc import Sequence
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings, normalize_database_url
from app.db.base import Base
from app.db.session import create_engine_and_sessionmaker
from app.main import create_app
from app.scrapers.base import BaseScraper, ProductRef, ScrapedOffer
from app.services.sync_service import SyncService

PG_URL = os.environ.get("RADARPRICE_TEST_POSTGRES_URL")
DEVICE = {"X-Device-Id": "pg-device-0000000000000001"}


@pytest.mark.parametrize(("raw", "expected"), [
    ("postgres://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
    ("postgresql://u:p@h/db?sslmode=require", "postgresql+asyncpg://u:p@h/db?ssl=require"),
    ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
    ("sqlite+aiosqlite:///./radarprice.db", "sqlite+aiosqlite:///./radarprice.db"),
])
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected
    assert Settings(database_url=raw).database_url == expected


class _LiveStore(BaseScraper):
    @property
    def store_name(self) -> str:
        return "Zalando"

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        if product.sku != "HQ8708":
            return []
        return [ScrapedOffer(size="42", price=Decimal("79.95"), in_stock=True, affiliate_url="https://z.test/p",
                             original_price=Decimal("120.00"))]


def _reset_schema(url: str, *, legacy: bool) -> None:
    """Esquema vacío; con `legacy`, como una BD previa sin las columnas `source`."""
    async def go() -> None:
        engine, _ = create_engine_and_sessionmaker(Settings(database_url=url))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            if legacy:
                await conn.execute(text("ALTER TABLE store_offers DROP COLUMN source"))
                await conn.execute(text("ALTER TABLE price_history DROP COLUMN source"))
        await engine.dispose()
    asyncio.run(go())


@pytest.mark.skipif(not PG_URL, reason="define RADARPRICE_TEST_POSTGRES_URL para probar contra PostgreSQL")
def test_full_flow_on_postgres() -> None:
    assert PG_URL is not None
    _reset_schema(PG_URL, legacy=True)
    settings = Settings(database_url=PG_URL, demo_data=False, sync_api_key="k", real_scrapers=())

    with TestClient(create_app(settings)) as client:  # arranque: añade columnas y siembra
        products = client.get("/api/v1/products").json()
        assert len(products) == 11 and all(p["sizeOffers"] == {} for p in products)

        alert = client.post("/api/v1/alerts", headers=DEVICE,
                            json={"sku": "HQ8708", "targetPrice": 80, "targetSize": "42"}).json()
        assert alert["createdAt"].endswith("Z") and alert["triggeredAt"] is None

        app = client.app
        service = SyncService(app.state.sessionmaker, [_LiveStore()], app.state.settings)  # type: ignore[attr-defined]
        report = client.portal.call(service.run)  # type: ignore[union-attr]
        assert (report.alerts_triggered, report.errors) == (1, [])

        offer = client.get("/api/v1/products/HQ8708").json()["sizeOffers"]["42"][0]
        assert (offer["source"], offer["price"], offer["originalPrice"]) == ("live", 79.95, 120.0)
        assert [p["price"] for p in client.get("/api/v1/products/HQ8708/history").json()] == [79.95]

    # Reinicio: los datos persisten y el seed no duplica.
    with TestClient(create_app(settings)) as client:
        assert len(client.get("/api/v1/products").json()) == 11
        fired = client.get("/api/v1/alerts", headers=DEVICE).json()
        assert len(fired) == 1 and fired[0]["triggeredPrice"] == 79.95
        assert client.delete(f"/api/v1/alerts/{fired[0]['id']}", headers=DEVICE).status_code == 204
