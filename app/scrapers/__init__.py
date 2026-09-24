from app.scrapers.base import BaseScraper, HttpScraper, ProductRef, ScrapedOffer, ScraperBlockedError, ScraperError
from app.scrapers.nike import NikeScraper
from app.scrapers.registry import SCRAPER_REGISTRY, build_default_scrapers
from app.scrapers.zalando import ZalandoScraper

__all__ = [
    "BaseScraper",
    "HttpScraper",
    "NikeScraper",
    "SCRAPER_REGISTRY",
    "ProductRef",
    "ScrapedOffer",
    "ScraperBlockedError",
    "ScraperError",
    "ZalandoScraper",
    "build_default_scrapers",
]
