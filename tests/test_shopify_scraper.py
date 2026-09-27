"""Scraper Shopify con fixtures calcadas de respuestas reales (sonda de sep. 2026, sin red).

Prueba en vivo: python -m app.scrapers.shopify www.urbanjunglestore.com HQ8708
"""
import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import StoreOffer
from app.db.session import create_engine_and_sessionmaker
from app.scrapers import SCRAPER_REGISTRY, build_default_scrapers
from app.scrapers.base import ProductRef, ScraperError
from app.scrapers.shopify import ShopifyScraper, product_matches_sku, sku_pattern
from app.seed.seed import seed_database
from app.services.sync_service import SyncService

CAMPUS = ProductRef(sku="HQ8708", brand="adidas", name="Campus 00s")
AF1 = ProductRef(sku="CW2288-111", brand="Nike", name="Air Force 1 '07")

# Urban Jungle: SKU del fabricante en variant.sku, tallas adidas en tercios.
CAMPUS_JS = {
    "handle": "adidas-campus-00s-core-black-hq8708",
    "options": [{"name": "Size", "position": 1}],
    "tags": ["FW24", "MEN"],
    "variants": [
        {"options": ["42"], "sku": "HQ8708_8", "price": 7200, "compare_at_price": 12000, "available": False},
        {"options": ["42 2/3"], "sku": "HQ8708_8_5", "price": 7200, "compare_at_price": 12000, "available": True},
        {"options": ["43 1/3"], "sku": "HQ8708_9", "price": 6900, "compare_at_price": 12000, "available": True},
        {"options": ["47 1/3"], "sku": "HQ8708_12", "price": 7200, "compare_at_price": None, "available": True},
    ],
}
# Asphaltgold: SKU solo en etiquetas ("Model:CW2288 111"), talla en la 1.ª de dos opciones.
AF1_JS = {
    "handle": "nike-air-force-1-07-white-white",
    "options": [{"name": "Size"}, {"name": "Color"}],
    "tags": ["Brand:Nike", "Model:CW2288 111", "nike250461"],
    "variants": [
        {"options": ["42.5", "White / White"], "sku": "nike250461-42-5", "price": 9900,
         "compare_at_price": 11900, "available": True},
        {"options": ["44", "White / White"], "sku": "nike250461-44", "price": 9900,
         "compare_at_price": 9900, "available": False},
    ],
}
# afew: solo tallas US → no utilizable.
US_ONLY_JS = {
    "handle": "nike-wmns-dunk-low-white-black-white",
    "options": [{"name": "Size"}],
    "description": "<dl hidden><dt>Style</dt><dd>DD1503-101</dd></dl>",
    "variants": [{"options": ["8.5"], "sku": "19143-8.5", "price": 6399, "available": True}],
}


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def _suggest(*handles: str) -> dict:
    return {"resources": {"results": {"products": [{"handle": h, "title": h} for h in handles]}}}


def _handler(products: dict[str, dict], suggest: dict[str, list[str]], currency: str = "EUR"):  # noqa: ANN202
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/cart.js":
            return httpx.Response(200, json={"currency": currency, "items": []})
        if path == "/search/suggest.json":
            return httpx.Response(200, json=_suggest(*suggest.get(request.url.params["q"], [])))
        if path.startswith("/products/") and path.endswith(".js"):
            handle_name = path.removeprefix("/products/").removesuffix(".js")
            if handle_name in products:
                return httpx.Response(200, json=products[handle_name])
        return httpx.Response(404, text="not found")
    return handle


def _fetch(handler, product: ProductRef, store: str = "Urban Jungle"):  # noqa: ANN001, ANN202
    scraper = ShopifyScraper(store, "shop.test", min_interval_seconds=0, backoff_seconds=0, max_retries=0)

    async def go():  # noqa: ANN202
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await scraper.fetch_offers(client, product)
    return run(go())


def test_sku_pattern_tolerates_separators_but_not_longer_codes() -> None:
    pattern = sku_pattern("CW2288-111")
    assert pattern is not None
    for text in ("CW2288-111", "Model:CW2288 111", "cw2288_111-42", "nike-air-force-1-cw2288111"):
        assert pattern.search(text), text
    for text in ("CW2288-1119", "XCW2288-111", "CW2288-112"):
        assert not pattern.search(text), text
    assert sku_pattern("A-12") is None  # demasiado corto: riesgo de falsos positivos


def test_matches_by_variant_sku_tags_or_description() -> None:
    assert product_matches_sku(CAMPUS_JS, "HQ8708")
    assert product_matches_sku(AF1_JS, "CW2288-111")
    assert product_matches_sku(US_ONLY_JS, "DD1503-101")
    assert not product_matches_sku(AF1_JS, "DD8959-100")


def test_parses_thirds_prices_in_cents_and_original_price() -> None:
    handler = _handler({"adidas-campus-00s-core-black-hq8708": CAMPUS_JS}, {"HQ8708": [CAMPUS_JS["handle"]]})
    offers = {o.size: o for o in _fetch(handler, CAMPUS)}
    assert list(offers) == ["42", "42.5", "43.5", "47.5"]
    assert (offers["42"].in_stock, offers["42.5"].in_stock) == (False, True)
    assert offers["43.5"].price == Decimal("69.00")
    assert offers["42.5"].original_price == Decimal("120.00")
    assert offers["47.5"].original_price is None
    assert offers["42"].affiliate_url == "https://shop.test/products/adidas-campus-00s-core-black-hq8708"


def test_skips_candidates_for_other_models() -> None:
    other = {**AF1_JS, "handle": "nike-air-force-1-other", "tags": ["Model:DD8959 100"]}
    handler = _handler(
        {"nike-air-force-1-other": other, AF1_JS["handle"]: AF1_JS},
        {"CW2288-111": ["nike-air-force-1-other", AF1_JS["handle"]]},
    )
    offers = _fetch(handler, AF1, "Asphaltgold")
    assert [(o.size, o.price, o.in_stock, o.original_price) for o in offers] == [
        ("42.5", Decimal("99.00"), True, Decimal("119.00")),
        ("44", Decimal("99.00"), False, None),  # compare_at == precio → sin rebaja
    ]


def test_no_match_is_empty_not_error() -> None:
    other = {**AF1_JS, "tags": ["Model:DD8959 100"]}
    assert _fetch(_handler({AF1_JS["handle"]: other}, {"CW2288-111": [AF1_JS["handle"]]}), AF1) == []
    assert _fetch(_handler({}, {}), AF1) == []


def test_us_only_sizes_and_foreign_currency_are_errors() -> None:
    handler = _handler({US_ONLY_JS["handle"]: US_ONLY_JS}, {"DD1503-101": [US_ONLY_JS["handle"]]})
    with pytest.raises(ScraperError, match="tallas EU"):
        _fetch(handler, ProductRef(sku="DD1503-101", brand="Nike", name="Dunk"))
    with pytest.raises(ScraperError, match="moneda 'GBP'"):
        _fetch(_handler({}, {}, currency="GBP"), AF1)


def test_registry_enables_shopify_stores_by_default() -> None:
    assert {"Urban Jungle", "Asphaltgold"} <= set(Settings().real_scrapers)
    scrapers = {s.store_name: s for s in build_default_scrapers(Settings(demo_data=False))}
    assert isinstance(scrapers["Urban Jungle"], ShopifyScraper)
    assert isinstance(SCRAPER_REGISTRY["Asphaltgold"](), ShopifyScraper)


def test_sync_persists_live_shopify_offers() -> None:
    handler = _handler({CAMPUS_JS["handle"]: CAMPUS_JS}, {"HQ8708": [CAMPUS_JS["handle"]]})

    async def go():  # noqa: ANN202
        settings = Settings(database_url="sqlite+aiosqlite:///:memory:", demo_data=False, real_scrapers=())
        engine, sessionmaker = create_engine_and_sessionmaker(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await seed_database(sessionmaker)
        scraper = ShopifyScraper("Urban Jungle", "shop.test", min_interval_seconds=0, backoff_seconds=0, max_retries=0)
        report = await SyncService(sessionmaker, [scraper], settings, transport=httpx.MockTransport(handler)).run()
        async with sessionmaker() as session:
            rows = (await session.scalars(select(StoreOffer).where(StoreOffer.store_name == "Urban Jungle"))).all()
        await engine.dispose()
        return report, rows

    report, rows = run(go())
    assert report.errors == []
    assert {(o.product_sku, o.size, o.source) for o in rows} >= {("HQ8708", "42.5", "live")}
    assert all(o.product_sku == "HQ8708" for o in rows)
