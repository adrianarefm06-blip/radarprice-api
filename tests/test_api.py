from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", sync_api_key="secret", real_scrapers=())
    with TestClient(create_app(settings)) as c:
        yield c


def test_deals_ranked_by_savings(client: TestClient) -> None:
    body = client.get("/api/v1/products/deals").json()
    assert [p["sku"] for p in body] == ["HQ8708", "DD1391-100", "DH6927-111"]
    first = body[0]
    assert set(first) >= {"id", "sku", "brand", "model", "imageUrl", "lowestPrice", "retailPrice", "sizeOffers"}
    assert first["lowestPrice"] == 89.95
    offer = first["sizeOffers"]["43"][0]
    assert set(offer) >= {"storeName", "storeLogoUrl", "price", "inStock", "affiliateUrl"}


def test_deals_by_size(client: TestClient) -> None:
    body = client.get("/api/v1/products/deals", params={"size": "43"}).json()
    assert body[0]["sku"] == "HQ8708"
    assert client.get("/api/v1/products/deals", params={"size": "27"}).status_code == 422


def test_product_detail_and_404(client: TestClient) -> None:
    body = client.get("/api/v1/products/dd1391-100").json()
    assert list(body["sizeOffers"]) == ["41", "42", "42.5", "43", "44"]
    assert all(len(v) >= 3 for v in body["sizeOffers"].values())
    missing = client.get("/api/v1/products/XXX-000")
    assert missing.status_code == 404 and missing.json()["sku"] == "XXX-000"


def test_history(client: TestClient) -> None:
    h30 = client.get("/api/v1/products/HQ8708/history", params={"days": 30}).json()
    h90 = client.get("/api/v1/products/HQ8708/history").json()
    assert len(h30) == 31 and len(h90) == 91
    assert h90[-31:] == h30
    assert len(h30[0]["date"]) == 10 and h30[-1]["price"] == 89.95


def test_search(client: TestClient) -> None:
    assert [p["sku"] for p in client.get("/api/v1/products", params={"q": "panda"}).json()] == ["DD1391-100"]


def test_sync_requires_key_and_updates(client: TestClient) -> None:
    assert client.post("/api/v1/sync").status_code == 401
    report = client.post("/api/v1/sync", headers={"X-API-Key": "secret"}).json()
    assert report["productsProcessed"] == 3
    assert report["offersUpserted"] == 50
    assert report["historyPointsUpserted"] == 3
    assert report["errors"] == []
