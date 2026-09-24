"""Scraper real de Nike.com (mercado ES).

  SKU en product_urls ──▶ GET PDP conocida ─────────────────────────────┐
  SKU desconocido ──▶ GET /es/w?q={SKU} ──┬─ ya es la PDP (redirección) ─┤
                                          └─ href /es/t/{slug}/{SKU} ─────┤
  (no encontrado: 404 / sin enlace / sin nodo)                            │
        └──▶ fallback GET /es/t/producto/{SKU} ───────────────────────────┤
                                                                          ▼
        <script id="__NEXT_DATA__"> → nodo con styleColor == SKU → sizes[] + prices

- La búsqueda de Nike se renderiza en cliente: sin JS no siempre incluye enlaces
  a la PDP. Por eso las URLs conocidas (KNOWN_PDP_URLS) van primero.
- El fallback solo se usa ante "no encontrado". Red, timeout o bloqueo se propagan
  sin más peticiones (no agravar un bloqueo ni duplicar esperas).
- Estructura no reconocida → ScraperError (el sync conserva las ofertas previas).
- Solo marcas Nike/Jordan; el resto devuelve [] sin hacer peticiones.

Prueba en vivo:
  python -m app.scrapers.nike HF5441-100
  python -m app.scrapers.nike DH6927-111 --brand Jordan
  python -m app.scrapers.nike HF5441-100 --url https://www.nike.com/es/t/…/HF5441-100
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.scrapers.base import HttpScraper, ProductRef, ScrapedOffer, ScraperBlockedError, ScraperError
from app.scrapers.common import normalize_size as _normalize_size
from app.scrapers.common import to_price as _to_price

_BASE_URL: Final = "https://www.nike.com"

# PDPs verificadas manualmente para el seed. Fuente única: registry.py las inyecta.
KNOWN_PDP_URLS: Final[Mapping[str, str]] = MappingProxyType({
    "HF5441-100": "https://www.nike.com/es/t/dunk-low-retro-zapatillas-hombre-GeHBr62V/HF5441-100",
    "DH6927-111": "https://www.nike.com/es/t/air-jordan-4-retro-zapatillas-hombre/DH6927-111",
})
_BRANDS: Final = frozenset({"nike", "jordan"})
_SKU_KEYS: Final = ("styleColor", "productCode", "styleCode")
_URL_KEYS: Final = ("pdpUrl", "canonicalUrl", "url")
_SIZE_LABEL_KEYS: Final = ("localizedLabel", "label", "localizedSize", "nikeSize", "size")
_PRICE_CONTAINERS: Final = ("prices", "price", "priceInfo")
_PRICE_KEYS: Final = ("currentPrice", "current", "salePrice", "fullPrice", "initialPrice", "value")
_STOCK_BOOL_KEYS: Final = ("inStock", "in_stock", "available", "isAvailable")
_IN_STOCK_STATUSES: Final = frozenset({"ACTIVE", "IN_STOCK", "AVAILABLE", "BUYABLE"})
_IN_STOCK_LEVELS: Final = frozenset({"HIGH", "MEDIUM", "LOW"})
_MAX_NODES: Final = 250_000
_EU_RANGE: Final = (28.0, 50.0)

_NEXT_DATA_RE: Final = re.compile(r"<script[^>]*\bid=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.S | re.I)
_CANONICAL_RE: Final = re.compile(r"<link[^>]+rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)[\"']", re.I)
_EU_LABEL_RE: Final = re.compile(r"\bEU\s*(\d{2}(?:[.,]5)?)(?!\d)", re.I)
_BARE_SIZE_RE: Final = re.compile(r"\d{2}(?:[.,]5)?")
# Acoplado al formato de HttpScraper._get: "{tienda}: HTTP {status}".
_NOT_FOUND_RE: Final = re.compile(r"HTTP (?:404|410)$")

_BROWSER_HEADERS: Final[Mapping[str, str]] = MappingProxyType({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
})


class NikeScraper(HttpScraper):
    default_headers = _BROWSER_HEADERS

    def __init__(
        self,
        *,
        marketplace_path: str = "es",
        product_urls: Mapping[str, str] | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        min_interval_seconds: float = 1.5,
    ) -> None:
        super().__init__(
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            min_interval_seconds=min_interval_seconds,
        )
        self._path = marketplace_path.strip("/")
        # URLs PDP fijadas por SKU (evitan el paso de búsqueda).
        self._product_urls = MappingProxyType({k.upper(): v for k, v in (product_urls or {}).items()})

    @property
    def store_name(self) -> str:
        return "Nike"

    def build_url(self, product: ProductRef) -> str | None:
        """URL del primer intento: PDP conocida o búsqueda. None si no es Nike/Jordan."""
        if product.brand.strip().lower() not in _BRANDS:
            return None
        return self._product_urls.get(product.sku.upper()) or f"{_BASE_URL}/{self._path}/w?q={quote(product.sku)}"

    def fallback_pdp_url(self, sku: str) -> str:
        """PDP con slug genérico: Nike suele redirigir al slug canónico."""
        return f"{_BASE_URL}/{self._path}/t/producto/{quote(sku)}"

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        first_url = self.build_url(product)
        if first_url is None:
            return []

        attempts: list[str] = []
        known_url = self._product_urls.get(product.sku.upper())
        if known_url is not None:
            offers = await self._try_pdp(client, product, known_url, attempts)
        else:
            offers = await self._try_search(client, product, first_url, attempts)
        if offers is not None:
            return offers

        fallback_url = self.fallback_pdp_url(product.sku)
        if fallback_url != known_url:
            offers = await self._try_pdp(client, product, fallback_url, attempts)
            if offers is not None:
                return offers

        raise ScraperError(f"Nike: {product.sku} no encontrado ({'; '.join(attempts)})")

    async def _try_search(
        self, client: httpx.AsyncClient, product: ProductRef, search_url: str, attempts: list[str],
    ) -> list[ScrapedOffer] | None:
        response = await self._get_or_none_if_missing(client, search_url, attempts)
        if response is None:
            return None
        if find_product_node(extract_next_data(response.text), product.sku) is not None:
            return self._parse_guarded(response.text, product, page_url=str(response.url))
        pdp_url = find_pdp_url(response.text, product.sku, self._path)
        if pdp_url is None:
            attempts.append(f"{search_url} → sin enlace a la PDP (render en cliente)")
            return None
        return await self._try_pdp(client, product, pdp_url, attempts)

    async def _try_pdp(
        self, client: httpx.AsyncClient, product: ProductRef, url: str, attempts: list[str],
    ) -> list[ScrapedOffer] | None:
        response = await self._get_or_none_if_missing(client, url, attempts)
        if response is None:
            return None
        if find_product_node(extract_next_data(response.text), product.sku) is None:
            attempts.append(f"{response.url} → sin nodo {product.sku} en __NEXT_DATA__")
            return None
        return self._parse_guarded(response.text, product, page_url=str(response.url))

    async def _get_or_none_if_missing(
        self, client: httpx.AsyncClient, url: str, attempts: list[str],
    ) -> httpx.Response | None:
        """404/410 → None (permite el siguiente intento). Bloqueo, red y 5xx se propagan."""
        try:
            return await self._get(client, url)
        except ScraperBlockedError:
            raise
        except ScraperError as exc:
            if _NOT_FOUND_RE.search(str(exc)) is None:
                raise
            attempts.append(f"{url} → {exc}")
            return None

    def parse(self, body: str, product: ProductRef) -> Sequence[ScrapedOffer]:
        return self._parse_guarded(body, product, page_url=None)

    @staticmethod
    def _parse_guarded(body: str, product: ProductRef, *, page_url: str | None) -> list[ScrapedOffer]:
        try:
            return parse_product_page(body, product.sku, page_url=page_url)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise ScraperError(f"Nike: datos inválidos para {product.sku} ({exc})") from exc


# -----------------------------------------------------------------------------
# Parsing (funciones puras, testeables con fixtures)
# -----------------------------------------------------------------------------

def parse_product_page(html: str, sku: str, *, page_url: str | None = None) -> list[ScrapedOffer]:
    data = extract_next_data(html)
    if data is None:
        raise ScraperError("Nike: __NEXT_DATA__ ausente (¿cambio de maquetación?)")
    node = find_product_node(data, sku)
    if node is None:
        raise ScraperError(f"Nike: {sku} no presente en la página")

    product_price = extract_price(node)
    url = _product_url(node, html, page_url, sku)
    offers: dict[str, ScrapedOffer] = {}
    for size_node in node["sizes"]:
        if not isinstance(size_node, dict):
            continue
        size = eu_size(size_node)
        if size is None:
            continue
        price = extract_price(size_node) or product_price
        if price is None:
            raise ScraperError(f"Nike: precio no encontrado para {sku}")
        in_stock = size_in_stock(size_node)
        current = offers.get(size)
        if current is None or (in_stock and not current.in_stock):
            offers[size] = ScrapedOffer(size=size, price=price, in_stock=in_stock, affiliate_url=url)

    if not offers:
        raise ScraperError(f"Nike: sin tallas EU reconocibles para {sku}")
    return sorted(offers.values(), key=lambda o: float(o.size))


def extract_next_data(html: str) -> Any | None:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def find_product_node(data: Any, sku: str) -> dict[str, Any] | None:
    """DFS iterativo (sin recursión) acotado a _MAX_NODES."""
    if data is None:
        return None
    target = sku.upper()
    stack: list[Any] = [data]
    visited = 0
    while stack and visited < _MAX_NODES:
        current = stack.pop()
        visited += 1
        if isinstance(current, dict):
            if isinstance(current.get("sizes"), list) and any(
                isinstance(current.get(k), str) and current[k].upper() == target for k in _SKU_KEYS
            ):
                return current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def extract_price(node: Mapping[str, Any]) -> Decimal | None:
    for container_key in _PRICE_CONTAINERS:
        container = node.get(container_key)
        if isinstance(container, Mapping):
            for key in _PRICE_KEYS:
                price = _to_price(container.get(key))
                if price is not None:
                    return price
        else:
            price = _to_price(container)
            if price is not None:
                return price
    for key in ("currentPrice", "salePrice"):
        price = _to_price(node.get(key))
        if price is not None:
            return price
    return None


def size_in_stock(size_node: Mapping[str, Any]) -> bool:
    for key in _STOCK_BOOL_KEYS:
        value = size_node.get(key)
        if isinstance(value, bool):
            return value
    status = size_node.get("status")
    if isinstance(status, str):
        return status.upper() in _IN_STOCK_STATUSES
    availability = size_node.get("availability")
    if isinstance(availability, Mapping):
        if isinstance(availability.get("isAvailable"), bool):
            return availability["isAvailable"]
        level = availability.get("level")
        if isinstance(level, str):
            return level.upper() in _IN_STOCK_LEVELS
    return False  # sin señal de stock → conservador


def eu_size(size_node: Mapping[str, Any]) -> str | None:
    """'EU 42.5' / 'EU 42,5' / '42.5' (rango EU) → '42.5'. Etiquetas US se descartan."""
    labels = [v for k in _SIZE_LABEL_KEYS if isinstance(v := size_node.get(k), str)]
    for label in labels:
        match = _EU_LABEL_RE.search(label)
        if match:
            return _normalize_size(match.group(1))
    for label in labels:
        if _BARE_SIZE_RE.fullmatch(label.strip()):
            size = _normalize_size(label.strip())
            if _EU_RANGE[0] <= float(size) <= _EU_RANGE[1]:
                return size
    return None


def find_pdp_url(html: str, sku: str, marketplace_path: str = "es") -> str | None:
    text = html.replace("\\u002F", "/").replace("\\/", "/")
    pattern = re.compile(
        rf"(?:https://www\.nike\.com)?/{re.escape(marketplace_path)}/t/[A-Za-z0-9\-_%.]+/{re.escape(sku)}(?![A-Za-z0-9-])",
        re.I,
    )
    match = pattern.search(text)
    return urljoin(_BASE_URL, match.group(0)) if match else None


def _is_nike_url(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "nike.com" or host.endswith(".nike.com"))


def _product_url(node: Mapping[str, Any], html: str, page_url: str | None, sku: str) -> str:
    candidates: list[str] = [v for k in _URL_KEYS if isinstance(v := node.get(k), str)]
    if canonical := _CANONICAL_RE.search(html):
        candidates.append(canonical.group(1))
    if page_url:
        candidates.append(page_url)
    for candidate in candidates:
        absolute = urljoin(_BASE_URL, candidate)
        if _is_nike_url(absolute):
            return absolute
    return f"{_BASE_URL}/es/w?q={quote(sku)}"


# -----------------------------------------------------------------------------
# CLI de diagnóstico
# -----------------------------------------------------------------------------

async def _probe(sku: str, brand: str, url: str | None) -> int:
    product_urls = {sku: url} if url else KNOWN_PDP_URLS
    scraper = NikeScraper(product_urls=product_urls)
    source = product_urls.get(sku.upper()) or "búsqueda + fallback"
    print(f"Nike · {sku} · origen: {source}", file=sys.stderr)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            offers = await scraper.fetch_offers(client, ProductRef(sku=sku, brand=brand, name=sku))
    except ScraperError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not offers:
        print("Sin ofertas (¿marca distinta de Nike/Jordan?)", file=sys.stderr)
        return 1
    for offer in offers:
        print(f"EU {offer.size:>5}  {offer.price:>8} €  {'stock' if offer.in_stock else 'agotado':<8} {offer.affiliate_url}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prueba en vivo del scraper Nike")
    parser.add_argument("sku", nargs="?", default="HF5441-100")
    parser.add_argument("--brand", default="Nike")
    parser.add_argument("--url", help="URL exacta de la PDP (por defecto: KNOWN_PDP_URLS)")
    args = parser.parse_args()
    sys.exit(asyncio.run(_probe(args.sku.upper(), args.brand, args.url)))
