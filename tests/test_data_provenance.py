"""Procedencia de datos: demo (simulado) vs producción (solo live) y sync periódico."""
import asyncio
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import SyncAlreadyRunningError
from app.main import create_app
from app.schemas.sync import SyncReportOut
from app.scrapers import build_default_scrapers
from app.scrapers.base import BaseScraper, ProductRef, ScrapedOffer
from app.services.scheduler import run_periodic_sync
from app.services.sync_service import SyncService

LIVE_SKU = "HQ8708"


class _LiveStore(BaseScraper):
    """Tienda "real" de test: solo vende HQ8708 en dos tallas."""

    @property
    def store_name(self) -> str:
        return "Zalando"

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        if product.sku != LIVE_SKU:
            return []
        url = "https://www.zalando.es/campus-ad115o1be-q11.html"
        return [
            ScrapedOffer(size="42", price=Decimal("84.95"), in_stock=True, affiliate_url=url),
            ScrapedOffer(size="43", price=Decimal("84.95"), in_stock=False, affiliate_url=url),
        ]


def _client(*, demo: bool) -> TestClient:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:", sync_api_key="k", real_scrapers=(), demo_data=demo,
    )
    return TestClient(create_app(settings))


def _run_live_sync(client: TestClient) -> None:
    app = client.app
    service = SyncService(app.state.sessionmaker, [_LiveStore()], app.state.settings)  # type: ignore[attr-defined]
    client.portal.call(service.run)  # type: ignore[union-attr]


@pytest.fixture
def demo() -> Iterator[TestClient]:
    with _client(demo=True) as c:
        yield c


@pytest.fixture
def prod() -> Iterator[TestClient]:
    with _client(demo=False) as c:
        yield c


def test_demo_marks_seeded_offers_as_simulated(demo: TestClient) -> None:
    body = demo.get("/api/v1/products/HQ8708").json()
    sources = {o["source"] for offers in body["sizeOffers"].values() for o in offers}
    assert sources == {"simulated"}
    assert len(demo.get("/api/v1/products/HQ8708/history", params={"days": 30}).json()) == 31


def test_production_hides_simulated_offers_and_synthetic_history(prod: TestClient) -> None:
    body = prod.get("/api/v1/products/HQ8708").json()
    assert body["sizeOffers"] == {}
    assert body["lowestPrice"] == body["retailPrice"]
    assert prod.get("/api/v1/products/HQ8708/history").json() == []
    assert all(p["sizeOffers"] == {} for p in prod.get("/api/v1/products").json())


def test_production_serves_only_live_offers_after_sync(prod: TestClient) -> None:
    _run_live_sync(prod)

    body = prod.get("/api/v1/products/HQ8708").json()
    assert list(body["sizeOffers"]) == ["42", "43"]
    assert {o["source"] for offers in body["sizeOffers"].values() for o in offers} == {"live"}
    assert body["lowestPrice"] == 84.95

    history = prod.get("/api/v1/products/HQ8708/history").json()
    assert [p["price"] for p in history] == [84.95]
    # Productos sin ofertas live: ni ofertas ni histórico.
    assert prod.get("/api/v1/products/HF5441-100/history").json() == []
    deals = prod.get("/api/v1/products/deals", params={"size": "42"}).json()
    assert [p["sku"] for p in deals] == [LIVE_SKU]


def test_demo_sync_keeps_simulated_history_label(demo: TestClient) -> None:
    _run_live_sync(demo)
    offers = demo.get("/api/v1/products/HQ8708").json()["sizeOffers"]["42"]
    assert {o["source"] for o in offers} == {"live", "simulated"}


def test_registry_without_demo_runs_only_real_scrapers() -> None:
    names = [s.store_name for s in build_default_scrapers(Settings(demo_data=False, real_scrapers=("Nike",)))]
    assert names == ["Nike"]
    assert len(build_default_scrapers(Settings(demo_data=True, real_scrapers=("Nike",)))) == 5


def test_sync_interval_validation() -> None:
    with pytest.raises(ValueError):
        Settings(sync_interval_minutes=1)
    assert Settings(sync_interval_minutes=360).sync_interval_minutes == 360


# --- planificador ---------------------------------------------------------------

class _FlakyService:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self) -> SyncReportOut:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("tienda caída")
        if self.calls == 2:
            raise SyncAlreadyRunningError()
        now = datetime.now(UTC)
        return SyncReportOut(started_at=now, finished_at=now, products_processed=0, errors=[],
                             offers_upserted=0, offers_marked_out_of_stock=0, history_points_upserted=0)


def test_periodic_sync_survives_failures_and_cancels_cleanly() -> None:
    async def go() -> int:
        service = _FlakyService()
        task = asyncio.create_task(run_periodic_sync(
            service, timedelta(milliseconds=5), initial_delay=timedelta(0),
        ))
        while service.calls < 4:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return service.calls

    assert asyncio.run(go()) >= 4


def test_periodic_sync_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError):
        asyncio.run(run_periodic_sync(_FlakyService(), timedelta(0)))


def test_app_starts_and_stops_scheduler() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", sync_interval_minutes=5, real_scrapers=())
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}
