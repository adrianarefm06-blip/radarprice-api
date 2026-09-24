"""Scraper Zalando con fixtures (sin red). Prueba en vivo: python -m app.scrapers.zalando HQ8708"""
import asyncio
import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import StoreOffer
from app.db.session import create_engine_and_sessionmaker
from app.scrapers import SCRAPER_REGISTRY, ZalandoScraper, build_default_scrapers
from app.scrapers.base import ProductRef, ScraperBlockedError, ScraperError
from app.scrapers.zalando import availability_in_stock, eu_size, find_pdp_url, page_mentions_sku, parse_product_page
from app.seed.catalog import SEED_PRODUCTS
from app.seed.seed import seed_database
from app.services.sync_service import SyncService

CAMPUS = ProductRef(sku="HQ8708", brand="adidas", name="Campus 00s")
PDP_PATH = "/adidas-originals-campus-00s-zapatillas-core-black-ad115o1be-q11.html"
PDP_URL = f"https://www.zalando.es{PDP_PATH}"


def _offer(size: str, price: str, availability: str, list_price: str | None = None) -> dict:
    offer: dict = {
        "@type": "Offer",
        "price": price,
        "priceCurrency": "EUR",
        "availability": f"http://schema.org/{availability}",
        "itemOffered": {"@type": "IndividualProduct", "size": size},
    }
    if list_price:
        offer["priceSpecification"] = [{"@type": "UnitPriceSpecification", "priceType": "ListPrice", "price": list_price}]
    return offer


def ld_html(sku: str = "HQ8708", offers: list[dict] | None = None) -> str:
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Campus 00s",
        "mpn": sku,
        "offers": offers if offers is not None else [
            _offer("41 1/3", "89.95", "InStock", "120.00"),
            _offer("42", "94.95", "OutOfStock", "120.00"),
            _offer("42 2/3", "92,95", "LimitedAvailability"),
            _offer("EU 44", "99.95", "InStock"),
            _offer("talla única", "99.95", "InStock"),
        ],
    }
    return (
        f'<html><head><link rel="canonical" href="{PDP_URL}"></head><body>'
        f'<script type="application/ld+json">{json.dumps(product)}</script></body></html>'
    )


def embedded_html(sku: str = "HQ8708") -> str:
    state = {"props": {"article": {"manufacturerSku": sku, "units": [
        {"size": "40", "price": {"amount": 8495}, "originalPrice": {"amount": 11995}, "available": True},
        {"size": "41", "price": {"amount": 8495}, "stock": 0},
        {"size": "42", "price": "89,95 €", "availability": "IN_STOCK"},
        {"size": "43", "price": 90},  # sin señal de stock → se ignora
    ]}}}
    return f'<html><body><script type="application/json" id="state">{json.dumps(state)}</script></body></html>'


def search_html() -> str:
    href = PDP_PATH.replace("/", "\\u002F")
    return f'<html><script>window.__catalog = {{"url":"{href}"}}</script><a href="/mujer/">Mujer</a></html>'


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    product: ProductRef = CAMPUS,
    **kwargs,  # noqa: ANN003
):  # noqa: ANN201
    async def go():  # noqa: ANN202
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            scraper = ZalandoScraper(min_interval_seconds=0, backoff_seconds=0, **kwargs)
            return await scraper.fetch_offers(client, product)
    return run(go())


# --- parser -------------------------------------------------------------------

def test_parse_json_ld_sizes_prices_stock_and_url() -> None:
    offers = {o.size: o for o in parse_product_page(ld_html(), "HQ8708")}
    assert list(offers) == ["41.5", "42", "42.5", "44"]
    assert offers["41.5"].price == Decimal("89.95") and offers["41.5"].in_stock
    assert offers["41.5"].original_price == Decimal("120.00")
    assert not offers["42"].in_stock
    assert offers["42.5"].price == Decimal("92.95") and offers["42.5"].in_stock
    assert offers["42.5"].original_price is None
    assert {o.affiliate_url for o in offers.values()} == {PDP_URL}


def test_parse_collision_keeps_cheapest_in_stock() -> None:
    html = ld_html(offers=[
        _offer("42 1/3", "99.95", "InStock"),
        _offer("42 2/3", "89.95", "OutOfStock"),
        _offer("42.5", "94.95", "InStock"),
    ])
    (offer,) = parse_product_page(html, "HQ8708")
    assert (offer.size, offer.price, offer.in_stock) == ("42.5", Decimal("94.95"), True)


def test_parse_embedded_json_fallback() -> None:
    offers = {o.size: o for o in parse_product_page(embedded_html(), "HQ8708")}
    assert list(offers) == ["40", "41", "42"]
    assert (offers["40"].price, offers["40"].original_price, offers["40"].in_stock) == (
        Decimal("84.95"), Decimal("119.95"), True,
    )
    assert offers["41"].in_stock is False
    assert offers["42"].price == Decimal("89.95") and offers["42"].in_stock
    assert offers["40"].affiliate_url == "https://www.zalando.es/catalogo/?q=HQ8708"


def test_parse_rejects_other_product_or_no_sizes() -> None:
    with pytest.raises(ScraperError, match="no corresponde"):
        parse_product_page(ld_html(sku="IF8770"), "HQ8708")
    with pytest.raises(ScraperError, match="sin tallas"):
        parse_product_page(ld_html(offers=[]), "HQ8708")
    # PDP fijada a mano: no exige que el SKU aparezca.
    assert parse_product_page(ld_html(sku="IF8770"), "HQ8708", verify_sku=False)


@pytest.mark.parametrize(("label", "expected"), [
    ("42", "42"), ("EU 42,5", "42.5"), ("42 1/3", "42.5"), ("42 2/3", "42.5"), ("44 1/3", "44.5"),
    (41, "41"), ("Talla EU 38", "38"), ("M", None), ("UK 8", None), ("12", None), (True, None),
])
def test_eu_size(label: object, expected: str | None) -> None:
    assert eu_size(label) == expected


def test_helpers() -> None:
    assert page_mentions_sku("mpn: hf5441 100", "HF5441-100")
    assert page_mentions_sku('"HF5441100"', "HF5441-100")
    assert not page_mentions_sku("HQ87080", "HQ8708")
    assert availability_in_stock("https://schema.org/InStock") is True
    assert availability_in_stock("SOLD_OUT") is False
    assert availability_in_stock("unknown") is None
    assert find_pdp_url(search_html()) == PDP_URL
    assert find_pdp_url("<html>sin resultados</html>") is None


# --- flujo HTTP ---------------------------------------------------------------

def test_fetch_search_then_pdp() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=search_html() if request.url.path == "/catalogo/" else ld_html())

    offers = fetch(handler)
    assert seen == ["/catalogo/", PDP_PATH]
    assert len(offers) == 4


def test_fetch_search_redirects_to_pdp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/catalogo/":
            return httpx.Response(302, headers={"Location": PDP_URL})
        return httpx.Response(200, text=ld_html())

    assert len(fetch(handler)) == 4


def test_fetch_known_pdp_skips_search() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=ld_html(sku="OTRO"))

    assert fetch(handler, product_urls={"hq8708": PDP_URL})
    assert seen == [PDP_PATH]


def test_fetch_without_results_raises() -> None:
    with pytest.raises(ScraperError, match="sin resultados"):
        fetch(lambda _: httpx.Response(200, text="<html>0 artículos</html>"))


@pytest.mark.parametrize("response", [
    httpx.Response(403, text="Forbidden"),
    httpx.Response(200, text='<script src="https://geo.captcha-delivery.com/c.js"></script>'),
])
def test_fetch_blocked(response: httpx.Response) -> None:
    with pytest.raises(ScraperBlockedError):
        fetch(lambda _: response)


# --- registry y sync ----------------------------------------------------------

def test_registry_exposes_zalando_opt_in() -> None:
    assert set(SCRAPER_REGISTRY) == {"Nike", "Zalando"}
    by_store = {s.store_name: s for s in build_default_scrapers(Settings(real_scrapers=("Zalando",)))}
    assert isinstance(by_store["Zalando"], ZalandoScraper)
    assert not isinstance(build_default_scrapers(Settings(real_scrapers=()))[0], ZalandoScraper)
    with pytest.raises(ValueError, match="no implementados"):
        build_default_scrapers(Settings(real_scrapers=("Foot Locker",)))


def _sync(handler: Callable[[httpx.Request], httpx.Response]):  # noqa: ANN202
    async def go():  # noqa: ANN202
        settings = Settings(database_url="sqlite+aiosqlite:///:memory:", real_scrapers=("Zalando",))
        engine, sessionmaker = create_engine_and_sessionmaker(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await seed_database(sessionmaker)

        async def zalando_offers() -> dict[tuple[str, str], tuple]:
            async with sessionmaker() as session:
                rows = await session.scalars(select(StoreOffer).where(StoreOffer.store_name == "Zalando"))
                return {(o.product_sku, o.size): (o.price, o.in_stock, o.original_price) for o in rows}

        before = await zalando_offers()
        scraper = ZalandoScraper(min_interval_seconds=0, backoff_seconds=0, max_retries=0)
        report = await SyncService(sessionmaker, [scraper], settings, transport=httpx.MockTransport(handler)).run()
        after = await zalando_offers()
        await engine.dispose()
        return before, after, report
    return run(go())


def test_sync_persists_real_zalando_offers_with_original_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sku = request.url.params.get("q")
        if sku == "HQ8708":
            return httpx.Response(200, text=search_html())
        if request.url.path == PDP_PATH:
            return httpx.Response(200, text=ld_html())
        return httpx.Response(200, text="<html>0 artículos</html>")

    before, after, report = _sync(handler)
    assert after[("HQ8708", "41.5")] == (Decimal("89.95"), True, Decimal("120.00"))
    assert after[("HQ8708", "42")][1] is False
    assert {e.sku for e in report.errors} == {p.sku for p in SEED_PRODUCTS} - {"HQ8708"}
    # Los SKUs con error conservan sus ofertas simuladas intactas.
    untouched = {k: v for k, v in before.items() if k[0] != "HQ8708"}
    assert {k: v for k, v in after.items() if k[0] != "HQ8708"} == untouched


def test_sync_fail_safe_when_zalando_blocks() -> None:
    before, after, report = _sync(lambda _: httpx.Response(403, text="Forbidden"))
    assert after == before
    assert report.errors and all(e.store_name == "Zalando" for e in report.errors)
    assert report.offers_upserted == 0
