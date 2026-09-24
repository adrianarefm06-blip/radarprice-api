from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from app.core.config import Settings
from app.core.stores import STORES
from app.scrapers.base import BaseScraper
from app.scrapers.nike import KNOWN_PDP_URLS, NikeScraper
from app.scrapers.simulated import SimulatedStoreScraper
from app.scrapers.zalando import KNOWN_PDP_URLS as ZALANDO_PDP_URLS
from app.scrapers.zalando import ZalandoScraper

# Tienda → fábrica del scraper real. Se activan con `settings.real_scrapers`;
# las no listadas (o desactivadas) usan simulación.
SCRAPER_REGISTRY: Final[Mapping[str, Callable[[], BaseScraper]]] = MappingProxyType({
    "Nike": lambda: NikeScraper(product_urls=KNOWN_PDP_URLS),
    "Zalando": lambda: ZalandoScraper(product_urls=ZALANDO_PDP_URLS),
})


def build_default_scrapers(settings: Settings) -> tuple[BaseScraper, ...]:
    """Real si la tienda está en `settings.real_scrapers`; simulada en caso contrario."""
    unknown = set(settings.real_scrapers) - set(SCRAPER_REGISTRY)
    if unknown:
        raise ValueError(f"Scrapers reales no implementados: {sorted(unknown)}")
    return tuple(
        SCRAPER_REGISTRY[name]() if name in settings.real_scrapers else SimulatedStoreScraper(name)
        for name in STORES
    )
