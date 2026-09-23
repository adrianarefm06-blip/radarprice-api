"""NikeScraper + fail-safe del sync, sin red (httpx.MockTransport).

Los fixtures reproducen la forma de __NEXT_DATA__ que el parser espera; validar
contra la web real con:  python -m app.scrapers.nike DD1391-100
"""
import asyncio
import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import PriceHistory, StoreOffer
from app.db.session import create_engine_and_sessionmaker
from app.scrapers import NikeScraper, ProductRef, ScraperBlockedError, ScraperError
from app.scrapers.nike import eu_size, find_pdp_url, parse_product_page
from app.scrapers.simulated import SimulatedStoreScraper
from app.seed.seed import seed_database
from app.services.sync_service import SyncService

PANDA = ProductRef(sku="DD1391-100", brand="Nike", name="Dunk Low")
PDP_URL = "https://www.nike.com/es/t/dunk-low-retro-zapatillas-hombre-Xyz123/DD1391-100"


def pdp_html(sku: str = "DD1391-100", price: float = 99.99) -> str:
    data = {
        "props": {"pageProps": {"productGroups": [{"products": {sku: {
            "styleColor": sku,
            "pdpUrl": {"url": "ignored"},
            "prices": {"currentPrice": price, "initialPrice": 119.99, "currency": "EUR"},
            "sizes": [
                {"label": "7", "localizedLabel": "EU 40", "status": "ACTIVE"},
                {"label": "8.5", "localizedLabel": "EU 42", "status": "OOS"},
                {"label": "9", "localizedLabel": "EU 42,5", "status": "ACTIVE"},
                {"label": "M 10 / W 11.5"},
                "basura",
            ],
        }}}]}},
    }
    return (
        f'<html><head><link rel="canonical" href="{PDP_URL}"></head><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'
    )


def search_html(sku: str = "DD1391-100") -> str:
    href = PDP_URL.replace("https://www.nike.com", "").replace("/", "\\u002F")
    return f'<html><script>window.x = {{"url":"{href}"}}</script><p>{sku}</p></html>'


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def fetch(handler: Callable[[httpx.Request], httpx.Response], product: ProductRef = PANDA):  # noqa: ANN201
    async def go():  # noqa: ANN202
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            return await NikeScraper(min_interval_seconds=0, backoff_seconds=0).fetch_offers(client, product)
    return run(go())


# --- parser -------------------------------------------------------------------

def test_parse_pdp_extracts_eu_sizes_price_stock_and_url() -> None:
    offers = parse_product_page(pdp_html(), "DD1391-100")
    assert [(o.size, o.price, o.in_stock) for o in offers] == [
        ("40", Decimal("99.99"), True),
        ("42", Decimal("99.99"), False),
        ("42.5", Decimal("99.99"), True),
    ]
    assert {o.affiliate_url for o in offers} == {PDP_URL}


@pytest.mark.parametrize(("node", "expected"), [
    ({"localizedLabel": "EU 44.5"}, "44.5"),
    ({"label": "38,5"}, "38.5"),
    ({"label": "M 9 / W 10.5"}, None),
    ({"label": "9"}, None),
])
def test_eu_size(node: dict[str, str], expected: str | None) -> None:
    assert eu_size(node) == expected


def test_unknown_structure_raises() -> None:
    with pytest.raises(ScraperError):
        parse_product_page("<html>sin datos</html>", "DD1391-100")
    with pytest.raises(ScraperError):
        parse_product_page(pdp_html(sku="OTHER-001"), "DD1391-100")


def test_find_pdp_url_handles_escaped_json() -> None:
    assert find_pdp_url(search_html(), "DD1391-100") == PDP_URL


# --- fetch --------------------------------------------------------------------

def test_search_then_pdp_with_browser_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=search_html() if "/w" in request.url.path else pdp_html())

    offers = fetch(handler)
    assert len(offers) == 3
    assert [r.url.path for r in seen] == ["/es/w", "/es/t/dunk-low-retro-zapatillas-hombre-Xyz123/DD1391-100"]
    assert seen[0].headers["accept-language"] == "es-ES,es;q=0.9"
    assert "Chrome/" in seen[0].headers["user-agent"]


def test_non_nike_brand_skips_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe haber peticiones")

    assert fetch(handler, ProductRef(sku="HQ8708", brand="adidas", name="Campus")) == []


@pytest.mark.parametrize("response", [
    httpx.Response(403, text="Forbidden"),
    httpx.Response(200, text="<html><title>Access Denied</title>Reference #18</html>"),
    httpx.Response(200, text='<script src="https://geo.captcha-delivery.com/c.js"></script>'),
])
def test_blocked_detection(response: httpx.Response) -> None:
    with pytest.raises(ScraperBlockedError):
        fetch(lambda _: response)


def test_timeout_exhausts_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    with pytest.raises(ScraperError, match="agotados reintentos"):
        fetch(handler)


# --- integración con SyncService ----------------------------------------------

def _sync(handler: Callable[[httpx.Request], httpx.Response]):  # noqa: ANN202
    async def go():  # noqa: ANN202
        settings = Settings(database_url="sqlite+aiosqlite:///:memory:", real_scrapers=("Nike",))
        engine, sessionmaker = create_engine_and_sessionmaker(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await seed_database(sessionmaker)
        async with sessionmaker() as session:
            before = {(o.product_sku, o.size): (o.price, o.in_stock)
                      for o in await session.scalars(select(StoreOffer).where(StoreOffer.store_name == "Nike"))}
        scrapers = [NikeScraper(min_interval_seconds=0, backoff_seconds=0), SimulatedStoreScraper("StockX")]
        report = await SyncService(sessionmaker, scrapers, settings, transport=httpx.MockTransport(handler)).run()
        async with sessionmaker() as session:
            after = {(o.product_sku, o.size): (o.price, o.in_stock, o.affiliate_url)
                     for o in await session.scalars(select(StoreOffer).where(StoreOffer.store_name == "Nike"))}
            history = (await session.scalars(select(PriceHistory))).all()
        await engine.dispose()
        return before, after, report, history
    return run(go())


def test_sync_persists_real_offers_and_history() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sku = request.url.params.get("q") or request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=pdp_html(sku=sku, price=89.99 if sku == "DD1391-100" else 205.0))

    _, after, report, history = _sync(handler)
    assert report.errors == []
    assert after[("DD1391-100", "42.5")] == (Decimal("89.99"), True, PDP_URL)
    assert after[("DD1391-100", "41")][1] is False  # talla ya no listada → agotada
    today_panda = [h for h in history if h.product_sku == "DD1391-100" and h.date == report.started_at.date()]
    assert today_panda and today_panda[0].price <= Decimal("89.99")


def test_sync_fail_safe_keeps_previous_nike_offers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    before, after, report, _ = _sync(handler)
    assert {(e.store_name, e.sku) for e in report.errors} == {("Nike", "DD1391-100"), ("Nike", "DH6927-111")}
    assert {k: v[:2] for k, v in after.items()} == before
    assert report.offers_upserted > 0  # StockX (simulado) sí se sincronizó
