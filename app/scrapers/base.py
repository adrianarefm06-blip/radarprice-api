"""Contrato de scrapers.

Flujo:  SyncService ──(httpx.AsyncClient compartido)──▶ scraper.fetch_offers(product) ──▶ [ScrapedOffer]
- Devuelve [] si la tienda no vende el producto.
- Lanza ScraperError ante fallo: el SyncService aísla el error y no toca las ofertas previas.
"""
import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import ClassVar

import httpx


class ScraperError(Exception):
    pass


class ScraperBlockedError(ScraperError):
    """Captcha / WAF / 401-403. No se reintenta: reintentar agrava el bloqueo."""


@dataclass(frozen=True, slots=True)
class ProductRef:
    sku: str
    brand: str
    name: str


@dataclass(frozen=True, slots=True)
class ScrapedOffer:
    size: str
    price: Decimal
    in_stock: bool
    affiliate_url: str
    # Precio tachado (PVP antes de rebaja). None = la tienda no lo informa.
    original_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.size.strip():
            raise ValueError("size vacío")
        if self.price <= 0:
            raise ValueError(f"precio inválido: {self.price}")
        if self.original_price is not None and self.original_price <= 0:
            raise ValueError(f"precio original inválido: {self.original_price}")
        if not self.affiliate_url.startswith(("https://", "http://")):
            raise ValueError(f"URL inválida: {self.affiliate_url}")


class BaseScraper(ABC):
    @property
    @abstractmethod
    def store_name(self) -> str: ...

    @property
    def is_live(self) -> bool:
        """True = precios leídos de la tienda real. Las simulaciones lo sobrescriben a False."""
        return True

    @abstractmethod
    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]: ...


class HttpScraper(BaseScraper, ABC):
    """Base para tiendas reales: GET con cabeceras propias, throttling, reintentos
    con backoff (red/429/5xx) y detección de captcha/WAF.

    Respetar robots.txt y ToS de cada tienda; preferir feeds de afiliación cuando existan.
    """

    default_headers: ClassVar[Mapping[str, str]] = MappingProxyType({})
    blocked_markers: ClassVar[tuple[str, ...]] = (
        "captcha-delivery.com",          # DataDome
        "px-captcha",                    # PerimeterX
        "cf-chl-",                       # Cloudflare challenge
        "hcaptcha.com/captcha",
        "<title>access denied</title>",  # Akamai
        "errors.edgesuite.net",          # Akamai
    )
    _retryable_status: ClassVar[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
    _blocked_status: ClassVar[frozenset[int]] = frozenset({401, 403})
    _marker_scan_bytes: ClassVar[int] = 50_000

    def __init__(
        self,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        min_interval_seconds: float = 0.0,
    ) -> None:
        if max_retries < 0 or backoff_seconds < 0 or min_interval_seconds < 0:
            raise ValueError("max_retries, backoff_seconds y min_interval_seconds deben ser >= 0")
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._min_interval_seconds = min_interval_seconds
        self._throttle_lock = asyncio.Lock()
        self._last_request_at = 0.0

    @abstractmethod
    def build_url(self, product: ProductRef) -> str | None:
        """None = la tienda no tiene este producto."""

    @abstractmethod
    def parse(self, body: str, product: ProductRef) -> Sequence[ScrapedOffer]: ...

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        url = self.build_url(product)
        if url is None:
            return []
        response = await self._get(client, url)
        try:
            return self.parse(response.text, product)
        except (ValueError, KeyError, TypeError) as exc:
            raise ScraperError(f"{self.store_name}: respuesta no parseable ({exc})") from exc

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        last_error: object = None
        for attempt in range(self._max_retries + 1):
            await self._throttle()
            try:
                response = await client.get(url, headers=dict(self.default_headers))
            except httpx.TransportError as exc:  # incluye timeouts
                last_error = exc
            else:
                status = response.status_code
                if status in self._blocked_status:
                    raise ScraperBlockedError(f"{self.store_name}: HTTP {status} (bloqueado)")
                if status in self._retryable_status:
                    last_error = f"HTTP {status}"
                elif status >= 400:
                    raise ScraperError(f"{self.store_name}: HTTP {status}")
                else:
                    self._raise_if_blocked(response)
                    return response
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * 2**attempt)
        raise ScraperError(f"{self.store_name}: agotados reintentos ({last_error!r})")

    async def _throttle(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        async with self._throttle_lock:
            wait = self._last_request_at + self._min_interval_seconds - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    def _raise_if_blocked(self, response: httpx.Response) -> None:
        head = response.text[: self._marker_scan_bytes].lower()
        marker = next((m for m in self.blocked_markers if m in head), None)
        if marker is not None:
            raise ScraperBlockedError(f"{self.store_name}: captcha/WAF detectado ({marker})")
