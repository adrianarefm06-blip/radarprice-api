"""Sincronización manual: scrapers en paralelo → upsert de ofertas → punto de histórico diario.

  ┌ snapshot productos ┐   ┌ scrape (semáforo N) ┐   ┌ 1 transacción ─────────────────────┐
  │ ProductRef[]       │ → │ tienda × producto   │ → │ upsert ofertas / stock=false stale │
  └────────────────────┘   └ errores aislados ───┘   │ upsert history(hoy) / poda antigua │
                                                     └────────────────────────────────────┘
"""
import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import SyncAlreadyRunningError
from app.db.models import PriceHistory, Product, StoreOffer
from app.schemas.sync import SyncErrorOut, SyncReportOut
from app.scrapers.base import BaseScraper, ProductRef, ScrapedOffer, ScraperError
from app.services.pricing import lowest_in_stock

_USER_AGENT = "RadarPriceBot/1.0 (+https://radarprice.app/bot)"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ScrapeResult:
    store_name: str
    sku: str
    offers: Sequence[ScrapedOffer] | None
    error: str | None


class SyncService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        scrapers: Sequence[BaseScraper],
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._scrapers = tuple(scrapers)
        self._settings = settings
        self._clock = clock
        self._transport = transport  # inyectable en tests (httpx.MockTransport)
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    async def run(self) -> SyncReportOut:
        if self._lock.locked():
            raise SyncAlreadyRunningError()
        async with self._lock:
            started_at = self._clock()
            products = await self._snapshot_products()
            results = await self._scrape_all(products)
            counters = await self._persist(products, results, started_at)
            return SyncReportOut(
                started_at=started_at,
                finished_at=self._clock(),
                products_processed=len(products),
                errors=[
                    SyncErrorOut(store_name=r.store_name, sku=r.sku, message=r.error)
                    for r in results if r.error is not None
                ],
                **counters,
            )

    async def _snapshot_products(self) -> list[ProductRef]:
        async with self._sessionmaker() as session:
            rows = (await session.scalars(select(Product).order_by(Product.sku))).all()
            return [ProductRef(sku=p.sku, brand=p.brand, name=p.name) for p in rows]

    async def _scrape_all(self, products: Sequence[ProductRef]) -> list[_ScrapeResult]:
        semaphore = asyncio.Semaphore(max(1, self._settings.scraper_concurrency))
        async with httpx.AsyncClient(
            timeout=self._settings.http_timeout_seconds,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            transport=self._transport,
        ) as client:

            async def scrape(scraper: BaseScraper, product: ProductRef) -> _ScrapeResult:
                async with semaphore:
                    try:
                        offers = await scraper.fetch_offers(client, product)
                        return _ScrapeResult(scraper.store_name, product.sku, offers, None)
                    except (ScraperError, ValueError, httpx.HTTPError) as exc:
                        # Fail-safe: error aislado; las ofertas previas de esta tienda/SKU no se tocan.
                        _logger.warning("sync %s/%s falló: %s", scraper.store_name, product.sku, exc)
                        return _ScrapeResult(scraper.store_name, product.sku, None, str(exc))

            return await asyncio.gather(*(scrape(s, p) for s in self._scrapers for p in products))

    async def _persist(
        self, products: Sequence[ProductRef], results: Sequence[_ScrapeResult], now: datetime,
    ) -> dict[str, int]:
        upserted = marked_out = history_upserted = 0
        today = now.date()

        async with self._sessionmaker() as session, session.begin():
            existing = {
                (o.product_sku, o.store_name, o.size): o
                for o in (await session.scalars(select(StoreOffer))).all()
            }
            for result in results:
                if result.offers is None:
                    continue  # fallo: se conservan las ofertas previas tal cual
                seen: set[str] = set()
                for scraped in result.offers:
                    seen.add(scraped.size)
                    key = (result.sku, result.store_name, scraped.size)
                    offer = existing.get(key)
                    if offer is None:
                        offer = StoreOffer(product_sku=result.sku, store_name=result.store_name, size=scraped.size)
                        session.add(offer)
                        existing[key] = offer
                    offer.price = scraped.price
                    offer.original_price = scraped.original_price
                    offer.in_stock = scraped.in_stock
                    offer.affiliate_url = scraped.affiliate_url
                    offer.last_updated = now
                    upserted += 1
                # Tallas que la tienda ya no lista → agotadas.
                for (sku, store, size), offer in existing.items():
                    if sku == result.sku and store == result.store_name and size not in seen and offer.in_stock:
                        offer.in_stock = False
                        offer.last_updated = now
                        marked_out += 1

            await session.flush()
            for product in products:
                offers = [o for (sku, _, _), o in existing.items() if sku == product.sku]
                lowest = lowest_in_stock(offers)
                if lowest is None:
                    continue
                history_upserted += await self._upsert_history(session, product.sku, today, lowest)

            cutoff = today - timedelta(days=self._settings.history_retention_days)
            await session.execute(delete(PriceHistory).where(PriceHistory.date < cutoff))

        return {
            "offers_upserted": upserted,
            "offers_marked_out_of_stock": marked_out,
            "history_points_upserted": history_upserted,
        }

    @staticmethod
    async def _upsert_history(session: AsyncSession, sku: str, day: date, price: Decimal) -> int:
        point = await session.scalar(
            select(PriceHistory).where(PriceHistory.product_sku == sku, PriceHistory.date == day)
        )
        if point is None:
            session.add(PriceHistory(product_sku=sku, date=day, price=price))
        else:
            point.price = price
        return 1
