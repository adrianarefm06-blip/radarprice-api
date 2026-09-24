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
    body = client.get("/api/v1/products/deals", params={"limit": 50}).json()
    assert len(body) == 11
    savings = [p["savingsPercent"] for p in body]
    assert savings == sorted(savings, reverse=True)
    first = body[0]
    assert set(first) >= {
        "id", "sku", "brand", "model", "imageUrl", "lowestPrice", "retailPrice", "sizeOffers", "gender", "colorway",
    }
    assert first["gender"] in {"men", "women", "unisex"} and first["colorway"]
    offer = next(iter(first["sizeOffers"].values()))[0]
    assert set(offer) >= {"storeName", "storeLogoUrl", "price", "inStock", "affiliateUrl"}


def test_deals_by_size(client: TestClient) -> None:
    body = client.get("/api/v1/products/deals", params={"size": "37.5", "limit": 50}).json()
    assert body and all(any(o["inStock"] for o in p["sizeOffers"]["37.5"]) for p in body)
    assert all(p["gender"] != "men" for p in body)  # 37.5 no existe en tallaje de hombre
    assert client.get("/api/v1/products/deals", params={"size": "27"}).status_code == 422


def test_catalog_covers_sizes_36_to_46_and_segments(client: TestClient) -> None:
    body = client.get("/api/v1/products/deals", params={"limit": 50}).json()
    sizes = {s for p in body for s in p["sizeOffers"]}
    assert {"36", "42.5", "46"} <= sizes
    assert {p["gender"] for p in body} == {"men", "women", "unisex"}
    stores = {o["storeName"] for p in body for offers in p["sizeOffers"].values() for o in offers}
    assert {"Nike", "Zalando", "Foot Locker", "StockX"} <= stores


def test_product_detail_and_404(client: TestClient) -> None:
    body = client.get("/api/v1/products/hf5441-100").json()
    assert list(body["sizeOffers"]) == ["39", "40", "40.5", "41", "42", "42.5", "43", "44", "44.5", "45", "46"]
    assert body["gender"] == "men" and body["colorway"] == "White/Black"
    assert all(len(v) >= 2 for v in body["sizeOffers"].values())
    missing = client.get("/api/v1/products/XXX-000")
    assert missing.status_code == 404 and missing.json()["sku"] == "XXX-000"


def test_history(client: TestClient) -> None:
    h30 = client.get("/api/v1/products/HQ8708/history", params={"days": 30}).json()
    h90 = client.get("/api/v1/products/HQ8708/history").json()
    assert len(h30) == 31 and len(h90) == 91
    assert h90[-31:] == h30
    lowest = client.get("/api/v1/products/HQ8708").json()["lowestPrice"]
    assert len(h30[0]["date"]) == 10 and h30[-1]["price"] == lowest


def test_search(client: TestClient) -> None:
    assert [p["sku"] for p in client.get("/api/v1/products", params={"q": "panda"}).json()] == ["HF5441-100"]


def test_sync_requires_key_and_updates(client: TestClient) -> None:
    assert client.post("/api/v1/sync").status_code == 401
    report = client.post("/api/v1/sync", headers={"X-API-Key": "secret"}).json()
    assert report["productsProcessed"] == 11
    assert report["offersUpserted"] > 0
    assert report["historyPointsUpserted"] == 11
    assert report["errors"] == []
