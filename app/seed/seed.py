"""Siembra la BD. Uso CLI:  python -m app.seed.seed [--reset]"""
import argparse
import asyncio
import hashlib
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.stores import STORES
from app.db.base import Base
from app.db.models import PriceHistory, Product, StoreOffer
from app.db.session import create_engine_and_sessionmaker
from app.seed.catalog import SEED_PRODUCTS, SeedProduct


async def seed_database(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    reset: bool = False,
    today: date | None = None,
    history_days: int = 365,
) -> bool:
    """Devuelve False si ya había datos y no se pidió reset."""
    today = today or date.today()
    now = datetime.now(UTC)
    async with sessionmaker() as session, session.begin():
        if reset:
            for table in (PriceHistory, StoreOffer, Product):
                await session.execute(delete(table))
        elif await session.scalar(select(func.count()).select_from(Product)):
            return False

        for seed in SEED_PRODUCTS:
            session.add(_product(seed, now))
            session.add_all(_history(seed, today, history_days))
    return True


def _product(seed: SeedProduct, now: datetime) -> Product:
    product = Product(
        sku=seed.sku, brand=seed.brand, name=seed.name,
        retail_price=seed.retail_price, image_url=seed.image_url,
    )
    product.offers = [
        StoreOffer(
            store_name=store, size=size, price=price, in_stock=in_stock,
            affiliate_url=STORES[store].affiliate_url(seed.sku), last_updated=now,
        )
        for store, sizes in seed.offers.items()
        for size, (price, in_stock) in sizes.items()
    ]
    return product


def _current_lowest(seed: SeedProduct) -> Decimal:
    prices = [p for sizes in seed.offers.values() for p, stock in sizes.values() if stock]
    return min(prices, default=seed.retail_price)


def _history(seed: SeedProduct, today: date, days: int) -> list[PriceHistory]:
    """Serie determinista por SKU: tendencia retail(+0-20%) → mínimo actual, con ruido y shocks."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.sku.encode()).digest()[:8], "big"))
    end = float(_current_lowest(seed))
    start = float(seed.retail_price) * (1 + rng.random() * 0.2)
    floor = end * 0.85
    shock = 0.0
    points: list[PriceHistory] = []
    for i in range(days + 1):
        trend = start + (end - start) * (i / days)
        if rng.random() < 0.06:
            shock = (-1 if rng.random() < 0.5 else 1) * end * (0.05 + rng.random() * 0.10)
        shock *= 0.8
        noise = (rng.random() - 0.5) * end * 0.03
        value = end if i == days else max(floor, trend + shock + noise)
        points.append(PriceHistory(
            product_sku=seed.sku,
            date=today - timedelta(days=days - i),
            price=Decimal(str(round(value, 2))),
        ))
    return points


async def _main(reset: bool) -> None:
    settings = get_settings()
    engine, sessionmaker = create_engine_and_sessionmaker(settings)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        seeded = await seed_database(sessionmaker, reset=reset)
        print("Seed aplicado" if seeded else "BD con datos: usa --reset para re-sembrar")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Siembra RadarPrice")
    parser.add_argument("--reset", action="store_true", help="Borra y vuelve a sembrar")
    asyncio.run(_main(parser.parse_args().reset))
