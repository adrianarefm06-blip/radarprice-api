"""Alertas por dispositivo: CRUD, aislamiento, validación y evaluación en el sync."""
from collections.abc import Iterator, Sequence
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1.alerts import MAX_ALERTS_PER_DEVICE
from app.core.config import Settings
from app.main import create_app
from app.scrapers.base import BaseScraper, ProductRef, ScrapedOffer
from app.services.sync_service import SyncService

ALICE = {"X-Device-Id": "alice-device-0000000001"}
BOB = {"X-Device-Id": "bob-device-00000000000002"}


class _PriceStore(BaseScraper):
    """Tienda real de test con precio configurable para HQ8708 talla 42."""

    def __init__(self, price: str) -> None:
        self.price = Decimal(price)

    @property
    def store_name(self) -> str:
        return "Zalando"

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        if product.sku != "HQ8708":
            return []
        return [ScrapedOffer(size="42", price=self.price, in_stock=True, affiliate_url="https://z.test/p")]


def _client(*, demo: bool) -> TestClient:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", sync_api_key="k", real_scrapers=(), demo_data=demo)
    return TestClient(create_app(settings))


def _sync(client: TestClient, price: str) -> int:
    app = client.app
    service = SyncService(app.state.sessionmaker, [_PriceStore(price)], app.state.settings)  # type: ignore[attr-defined]
    return client.portal.call(service.run).alerts_triggered  # type: ignore[union-attr]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client(demo=True) as c:
        yield c


@pytest.fixture
def prod() -> Iterator[TestClient]:
    with _client(demo=False) as c:
        yield c


def _create(client: TestClient, headers: dict[str, str] = ALICE, **body: object) -> httpx.Response:
    return client.post("/api/v1/alerts", headers=headers, json={"sku": "HQ8708", "targetPrice": 50, **body})


def test_crud_and_device_isolation(client: TestClient) -> None:
    created = _create(client, targetSize="42")
    assert created.status_code == 201
    alert = created.json()
    assert alert["id"].startswith("alt_") and alert["productId"] == "prd_hq8708"
    assert (alert["isActive"], alert["targetSize"], alert["triggeredAt"]) == (True, "42", None)
    assert alert["currentPrice"] is not None

    assert [a["id"] for a in client.get("/api/v1/alerts", headers=ALICE).json()] == [alert["id"]]
    assert client.get("/api/v1/alerts", headers=BOB).json() == []
    # Otro dispositivo no puede ver, modificar ni borrar (404, sin revelar existencia).
    url = f"/api/v1/alerts/{alert['id']}"
    assert client.patch(url, headers=BOB, json={"isActive": False}).status_code == 404
    assert client.delete(url, headers=BOB).status_code == 404

    paused = client.patch(url, headers=ALICE, json={"isActive": False}).json()
    assert paused["isActive"] is False
    assert client.patch(url, headers=ALICE, json={"targetPrice": 60}).json()["targetPrice"] == 60

    assert client.delete(url, headers=ALICE).status_code == 204
    assert client.get("/api/v1/alerts", headers=ALICE).json() == []


def test_validation(client: TestClient) -> None:
    assert client.get("/api/v1/alerts").status_code == 422  # sin X-Device-Id
    assert client.get("/api/v1/alerts", headers={"X-Device-Id": "corto"}).status_code == 422
    assert _create(client, targetPrice=0).status_code == 422
    assert _create(client, targetSize="27").status_code == 422
    assert _create(client, sku="XXX-000").status_code == 404
    assert _create(client).status_code == 201
    assert _create(client).status_code == 409  # misma zapatilla y talla (cualquiera)
    assert _create(client, targetSize="42").status_code == 201  # otra talla: permitida
    assert client.patch("/api/v1/alerts/alt_nope", headers=ALICE, json={}).status_code == 422


def test_limit_per_device(client: TestClient) -> None:
    skus = [p["sku"] for p in client.get("/api/v1/products").json()]
    sizes = [None, "36", "38", "39", "40", "41", "42", "43", "44", "45", "46"]
    combos = [(sku, size) for sku in skus for size in sizes][:MAX_ALERTS_PER_DEVICE]
    for sku, size in combos:
        assert _create(client, sku=sku, targetSize=size).status_code == 201
    assert _create(client, sku=skus[-1], targetSize="37.5").status_code == 409
    assert _create(client, BOB).status_code == 201  # el límite es por dispositivo


def test_created_below_target_is_born_triggered(client: TestClient) -> None:
    alert = _create(client, targetPrice=5000).json()
    assert alert["triggeredAt"] is not None
    # Fechas siempre con zona (SQLite las devuelve sin ella).
    listed = client.get("/api/v1/alerts", headers=ALICE).json()[0]
    assert listed["createdAt"].endswith("Z") and listed["triggeredAt"].endswith("Z")
    assert alert["triggeredPrice"] == alert["currentPrice"]


def test_sync_triggers_once_and_rearms_in_production(prod: TestClient) -> None:
    alert = _create(prod, targetPrice=80, targetSize="42").json()
    assert alert["currentPrice"] is None and alert["triggeredAt"] is None  # sin ofertas live aún

    assert _sync(prod, "85.00") == 0
    assert prod.get("/api/v1/alerts", headers=ALICE).json()[0]["triggeredAt"] is None

    assert _sync(prod, "79.95") == 1
    fired = prod.get("/api/v1/alerts", headers=ALICE).json()[0]
    assert (fired["triggeredPrice"], fired["currentPrice"]) == (79.95, 79.95)

    assert _sync(prod, "75.00") == 0  # ya disparada: no se repite el aviso
    assert _sync(prod, "90.00") == 0  # vuelve a subir: se rearma
    assert prod.get("/api/v1/alerts", headers=ALICE).json()[0]["triggeredAt"] is None
    assert _sync(prod, "78.00") == 1  # nuevo cruce: nuevo aviso


def test_paused_alerts_are_not_evaluated(prod: TestClient) -> None:
    alert = _create(prod, targetPrice=80, targetSize="42").json()
    prod.patch(f"/api/v1/alerts/{alert['id']}", headers=ALICE, json={"isActive": False})
    assert _sync(prod, "70.00") == 0
    assert prod.get("/api/v1/alerts", headers=ALICE).json()[0]["triggeredAt"] is None
