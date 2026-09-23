from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from app.core.config import Settings
from app.core.stores import STORES
from app.scrapers.base import BaseScraper
from app.scrapers.nike import KNOWN_PDP_URLS, NikeScraper
from app.scrapers.simulated import SimulatedStoreScraper

# Tienda → fábrica del scraper real. Las no listadas (o desactivadas) usan simulación.
_REAL_SCRAPERS: Final[Mapping[str, Callable[[], BaseScraper]]] = MappingProxyType({
    "Nike": lambda: NikeScraper(product_urls=KNOWN_PDP_URLS),
})


def build_default_scrapers(settings: Settings) -> tuple[BaseScraper, ...]:
    """Real si la tienda está en `settings.real_scrapers`; simulada en caso contrario."""
    unknown = set(settings.real_scrapers) - set(_REAL_SCRAPERS)
    if unknown:
        raise ValueError(f"Scrapers reales no implementados: {sorted(unknown)}")
    return tuple(
        _REAL_SCRAPERS[name]() if name in settings.real_scrapers else SimulatedStoreScraper(name)
        for name in STORES
    )
