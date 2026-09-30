"""Fotos de producto: extracción en los scrapers, prioridad en el sync y corrección del seed."""
import asyncio
from collections.abc import Sequence
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Product
from app.db.session import create_engine_and_sessionmaker
from app.main import create_app
from app.scrapers.base import BaseScraper, ProductRef, ScrapedOffer, ScraperError
from app.scrapers.nike import extract_image
from app.scrapers.shopify import featured_image, parse_product
from app.seed.catalog import FALLBACK_IMAGES, LEGACY_IMAGE_CDN, SEED_PRODUCTS
from app.seed.seed import seed_database
from app.services.sync_service import SyncService

NIKE_IMG = "https://static.nike.com/a/images/t_default/abc/NIKE+AIR+FORCE+1.png"
SHOP_IMG = "https://cdn.shopify.com/s/files/1/x/files/cw2288-111.webp?v=1&width=800"


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


# --- extracción ------------------------------------------------------------------

def test_nike_og_image() -> None:
    html = f'<head><meta property="og:image" content="{NIKE_IMG.replace("&", "&amp;")}"></head>'
    assert extract_image(html) == NIKE_IMG
    assert extract_image('<meta property="og:image" content="http://insecure.test/a.png">') is None
    assert extract_image("<html></html>") is None


def test_shopify_featured_image_is_https_and_resized() -> None:
    assert featured_image({"featured_image": "//cdn.shopify.com/a.webp?v=1"}) == \
        "https://cdn.shopify.com/a.webp?v=1&width=800"
    assert featured_image({"featured_image": "https://cdn.shopify.com/a.webp?width=2000"}) == \
        "https://cdn.shopify.com/a.webp?width=800"
    assert featured_image({"featured_image": None}) is None
    offers = parse_product(
        {"featured_image": "//cdn.shopify.com/a.webp", "options": ["Size"],
         "variants": [{"options": ["42"], "price": 9900, "available": True}]},
        url="https://shop.test/products/a", store="Shop",
    )
    assert offers[0].image_url == "https://cdn.shopify.com/a.webp?width=800"


def test_offer_rejects_insecure_image() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ScrapedOffer(size="42", price=Decimal("90"), in_stock=True, affiliate_url="https://s.test",
                     image_url="http://s.test/a.png")


# --- sync --------------------------------------------------------------------------

class _Store(BaseScraper):
    def __init__(self, name: str, image: str | None, *, live: bool = True, fail: bool = False) -> None:
        self._name, self._image, self._live, self._fail = name, image, live, fail

    @property
    def store_name(self) -> str:
        return self._name

    @property
    def is_live(self) -> bool:
        return self._live

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        if self._fail:
            raise ScraperError(f"{self._name}: caída")
        if product.sku != "CW2288-111":
            return []
        return [ScrapedOffer(size="42", price=Decimal("99"), in_stock=True,
                             affiliate_url="https://s.test/p", image_url=self._image)]


def _sync_images(*scrapers: BaseScraper, before: str | None = None) -> tuple[str, int]:
    async def go() -> tuple[str, int]:
        settings = Settings(database_url="sqlite+aiosqlite:///:memory:", demo_data=False, real_scrapers=())
        engine, sessionmaker = create_engine_and_sessionmaker(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await seed_database(sessionmaker)
        if before is not None:
            async with sessionmaker() as session, session.begin():
                await session.execute(update(Product).where(Product.sku == "CW2288-111").values(image_url=before))
        report = await SyncService(sessionmaker, list(scrapers), settings).run()
        async with sessionmaker() as session:
            image = await session.scalar(select(Product.image_url).where(Product.sku == "CW2288-111"))
        await engine.dispose()
        return image or "", report.images_updated
    return run(go())


def test_sync_prefers_first_live_store_image() -> None:
    # Orden de los scrapers = prioridad (la tienda oficial va primero en STORES).
    assert _sync_images(_Store("Nike", NIKE_IMG), _Store("Asphaltgold", SHOP_IMG)) == (NIKE_IMG, 1)
    assert _sync_images(_Store("Nike", None), _Store("Asphaltgold", SHOP_IMG)) == (SHOP_IMG, 1)


def test_sync_ignores_simulated_and_keeps_image_on_failure() -> None:
    assert _sync_images(_Store("StockX", SHOP_IMG, live=False)) == ("", 0)
    assert _sync_images(_Store("Nike", NIKE_IMG, fail=True), before=SHOP_IMG) == (SHOP_IMG, 0)
    assert _sync_images(_Store("Nike", NIKE_IMG), before=NIKE_IMG) == (NIKE_IMG, 0)  # sin cambios


# --- seed ----------------------------------------------------------------------------

def test_fallback_images_are_https_and_known_skus() -> None:
    skus = {p.sku for p in SEED_PRODUCTS}
    assert set(FALLBACK_IMAGES) <= skus
    assert all(url.startswith("https://") for url in FALLBACK_IMAGES.values())
    assert not any(p.image_url.startswith(LEGACY_IMAGE_CDN) for p in SEED_PRODUCTS)


def test_startup_replaces_dead_cdn_images() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", real_scrapers=())
    with TestClient(create_app(settings)) as client:
        sessionmaker = client.app.state.sessionmaker  # type: ignore[attr-defined]

        async def legacy() -> None:
            async with sessionmaker() as session, session.begin():
                await session.execute(update(Product).values(image_url=f"{LEGACY_IMAGE_CDN}products/x.webp"))
            await seed_database(sessionmaker)  # lo que hace el arranque sobre una BD existente

        client.portal.call(legacy)  # type: ignore[union-attr]
        products = {p["sku"]: p for p in client.get("/api/v1/products").json()}
        assert products["DD1503-101"]["imageUrl"] == FALLBACK_IMAGES["DD1503-101"]
        assert products["CW2288-111"]["imageUrl"] == ""  # la pondrá el sync real
        assert all(not p["imageUrl"].startswith(LEGACY_IMAGE_CDN) for p in products.values())
        offer = next(o for offers in products["HQ8708"]["sizeOffers"].values() for o in offers)
        assert offer["storeLogoUrl"] == ""


def test_privacy_page_is_public_html() -> None:
    with TestClient(create_app(Settings(database_url="sqlite+aiosqlite:///:memory:", real_scrapers=()))) as client:
        response = client.get("/privacidad")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Política de privacidad" in response.text and "<script" not in response.text
