"""Scraper real de Zalando España.

  SKU en product_urls ──▶ GET PDP conocida (verificada a mano) ──────────────┐
  SKU desconocido ──▶ GET /catalogo/?q={SKU} ──▶ 1er enlace *-XXXXXXXXX-XXX.html ┤
  (404 / sin enlace) ──▶ ScraperError                                       │
                                                                            ▼
  PDP ─▶ ¿aparece el SKU del fabricante? (salvo PDP conocida) ── no ─▶ ScraperError
      ─▶ JSON-LD schema.org/Product · offers por talla
      ─▶ (sin tallas) JSON embebido: nodos {talla, precio, stock}
      ─▶ [ScrapedOffer(size EU, price, original_price, in_stock)]

- Tallas con tercios (adidas "42 2/3") se redondean a la media talla más cercana
  para casar con el catálogo; si dos colisionan gana la más barata en stock.
- Nunca se inventa stock: sin señal de disponibilidad → agotado.
- Bloqueo (captcha/WAF, 401/403), red o estructura desconocida → ScraperError:
  el SyncService aísla el fallo y conserva las ofertas previas de Zalando.

Prueba en vivo:
  python -m app.scrapers.zalando HQ8708
  python -m app.scrapers.zalando HQ8708 --url https://www.zalando.es/…-ad115o1be-q11.html
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.scrapers.base import HttpScraper, ProductRef, ScrapedOffer, ScraperBlockedError, ScraperError
from app.scrapers.common import normalize_size, to_price

_BASE_URL: Final = "https://www.zalando.es"

# PDPs verificadas manualmente. Vacío hasta validar contra la web real.
KNOWN_PDP_URLS: Final[Mapping[str, str]] = MappingProxyType({})

_EU_RANGE: Final = (28.0, 50.0)
_MAX_NODES: Final = 250_000
_SIZE_KEYS: Final = ("size", "sizeLabel", "displaySize", "sizeName", "label", "name")
_PRICE_KEYS: Final = ("price", "currentPrice", "promotionalPrice", "salePrice", "lowPrice", "amount", "value")
_ORIGINAL_PRICE_KEYS: Final = ("originalPrice", "original", "listPrice", "regularPrice", "priceOriginal", "highPrice")
_STOCK_BOOL_KEYS: Final = ("available", "isAvailable", "inStock", "isInStock", "buyable")
_STOCK_QTY_KEYS: Final = ("stock", "quantity", "stockQuantity")
_IN_STOCK_TOKENS: Final = ("instock", "limitedavailability", "onlineonly", "available")
_OUT_OF_STOCK_TOKENS: Final = ("outofstock", "soldout", "discontinued", "unavailable", "preorder")

_LD_JSON_RE: Final = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_JSON_SCRIPT_RE: Final = re.compile(r"<script[^>]*type=[\"']application/json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_CANONICAL_RE: Final = re.compile(r"<link[^>]+rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)[\"']", re.I)
_PDP_HREF_RE: Final = re.compile(r"(?:https://www\.zalando\.es)?/[a-z0-9][a-z0-9-]*-[a-z0-9]{9}-[a-z0-9]{3}\.html", re.I)
_SIZE_RE: Final = re.compile(r"^(?:EU\s*)?(\d{2})(?:([.,]5)|\s+([12])/3)?$", re.I)
_EU_IN_TEXT_RE: Final = re.compile(r"\bEU\s*(\d{2}(?:[.,]5)?(?:\s+[12]/3)?)\b", re.I)
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


class ZalandoScraper(HttpScraper):
    default_headers = _BROWSER_HEADERS

    def __init__(
        self,
        *,
        product_urls: Mapping[str, str] | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        min_interval_seconds: float = 2.0,
    ) -> None:
        super().__init__(
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            min_interval_seconds=min_interval_seconds,
        )
        self._product_urls = MappingProxyType({k.upper(): v for k, v in (product_urls or {}).items()})

    @property
    def store_name(self) -> str:
        return "Zalando"

    def build_url(self, product: ProductRef) -> str | None:
        return self._product_urls.get(product.sku.upper()) or f"{_BASE_URL}/catalogo/?q={quote(product.sku)}"

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        known_url = self._product_urls.get(product.sku.upper())
        if known_url is not None:
            response = await self._get(client, known_url)
            return self._parse_guarded(response.text, product, page_url=str(response.url), verify_sku=False)

        search_url = f"{_BASE_URL}/catalogo/?q={quote(product.sku)}"
        search = await self._get_or_none_if_missing(client, search_url)
        if search is None:
            raise ScraperError(f"Zalando: búsqueda de {product.sku} no disponible (404)")
        # Algunas búsquedas por SKU exacto redirigen directamente a la PDP.
        if is_zalando_pdp_url(str(search.url)):
            return self._parse_guarded(search.text, product, page_url=str(search.url), verify_sku=True)
        pdp_url = find_pdp_url(search.text)
        if pdp_url is None:
            raise ScraperError(f"Zalando: {product.sku} sin resultados en la búsqueda")
        pdp = await self._get(client, pdp_url)
        return self._parse_guarded(pdp.text, product, page_url=str(pdp.url), verify_sku=True)

    async def _get_or_none_if_missing(self, client: httpx.AsyncClient, url: str) -> httpx.Response | None:
        try:
            return await self._get(client, url)
        except ScraperBlockedError:
            raise
        except ScraperError as exc:
            if _NOT_FOUND_RE.search(str(exc)) is None:
                raise
            return None

    def parse(self, body: str, product: ProductRef) -> Sequence[ScrapedOffer]:
        return self._parse_guarded(body, product, page_url=None, verify_sku=True)

    @staticmethod
    def _parse_guarded(body: str, product: ProductRef, *, page_url: str | None, verify_sku: bool) -> list[ScrapedOffer]:
        try:
            return parse_product_page(body, product.sku, page_url=page_url, verify_sku=verify_sku)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise ScraperError(f"Zalando: datos inválidos para {product.sku} ({exc})") from exc


# -----------------------------------------------------------------------------
# Parsing (funciones puras, testeables con fixtures)
# -----------------------------------------------------------------------------

def parse_product_page(
    html: str, sku: str, *, page_url: str | None = None, verify_sku: bool = True,
) -> list[ScrapedOffer]:
    if verify_sku and not page_mentions_sku(html, sku):
        raise ScraperError(f"Zalando: la página no corresponde a {sku}")
    url = _product_url(html, page_url, sku)

    raw = list(_ld_offers(html)) or list(_embedded_units(html))
    offers: dict[str, ScrapedOffer] = {}
    for size, price, original, in_stock in raw:
        candidate = ScrapedOffer(
            size=size,
            price=price,
            in_stock=in_stock,
            affiliate_url=url,
            original_price=original if original is not None and original > price else None,
        )
        current = offers.get(size)
        if current is None or _better(candidate, current):
            offers[size] = candidate
    if not offers:
        raise ScraperError(f"Zalando: sin tallas EU reconocibles para {sku}")
    return sorted(offers.values(), key=lambda o: float(o.size))


def page_mentions_sku(html: str, sku: str) -> bool:
    """SKU del fabricante presente (HQ8708 ≈ hq8708 · HF5441-100 ≈ HF5441 100 / HF5441100)."""
    parts = [re.escape(p) for p in re.split(r"[-\s]+", sku.strip()) if p]
    if not parts:
        return False
    pattern = re.compile(r"(?<![A-Za-z0-9])" + r"[-\s]?".join(parts) + r"(?![A-Za-z0-9])", re.I)
    return pattern.search(html) is not None


def find_pdp_url(html: str) -> str | None:
    text = html.replace("\\u002F", "/").replace("\\/", "/")
    match = _PDP_HREF_RE.search(text)
    return urljoin(_BASE_URL, match.group(0)) if match else None


def is_zalando_pdp_url(url: str) -> bool:
    parts = urlsplit(url)
    return _is_zalando_host(parts.hostname or "") and _PDP_HREF_RE.fullmatch(parts.path) is not None


def eu_size(label: Any) -> str | None:
    """'42' / 'EU 42,5' / '42 2/3' → talla EU con medias ('42.5'). Fuera de rango → None."""
    if not isinstance(label, (str, int, float)) or isinstance(label, bool):
        return None
    text = str(label).strip()
    match = _SIZE_RE.match(text)
    if match is None and (embedded := _EU_IN_TEXT_RE.search(text)) is not None:
        match = _SIZE_RE.match(embedded.group(1))
    if match is None:
        return None
    whole, half, third = match.groups()
    value = float(whole) + (0.5 if half else 0.0) + (int(third) / 3 if third else 0.0)
    value = round(value * 2) / 2  # tercios → media talla más cercana
    if not _EU_RANGE[0] <= value <= _EU_RANGE[1]:
        return None
    return normalize_size(f"{value:.1f}")


def availability_in_stock(value: Any) -> bool | None:
    """schema.org availability / estados textuales. None = sin señal."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    token = value.rsplit("/", 1)[-1].replace("_", "").replace(" ", "").lower()
    if any(t in token for t in _OUT_OF_STOCK_TOKENS):
        return False
    if any(t in token for t in _IN_STOCK_TOKENS):
        return True
    return None


# --- JSON-LD ------------------------------------------------------------------

_Raw = tuple[str, Decimal, Decimal | None, bool]


def _ld_offers(html: str) -> Iterator[_Raw]:
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if not _is_type(node, "Product"):
                continue
            offers = node.get("offers")
            if isinstance(offers, Mapping) and isinstance(offers.get("offers"), list):
                offers = offers["offers"]  # AggregateOffer
            for offer in offers if isinstance(offers, list) else [offers]:
                if isinstance(offer, Mapping) and (raw := _ld_offer(offer)) is not None:
                    yield raw


def _ld_offer(offer: Mapping[str, Any]) -> _Raw | None:
    item = offer.get("itemOffered") if isinstance(offer.get("itemOffered"), Mapping) else {}
    size = _first_size(offer) or _first_size(item) or _size_from_properties(offer) or _size_from_properties(item)
    price = to_price(offer.get("price")) or _spec_price(offer, list_price=False)
    if size is None or price is None:
        return None
    original = _spec_price(offer, list_price=True) or _first_price(offer, _ORIGINAL_PRICE_KEYS)
    return size, price, original, availability_in_stock(offer.get("availability")) is True


def _spec_price(offer: Mapping[str, Any], *, list_price: bool) -> Decimal | None:
    specs = offer.get("priceSpecification")
    for spec in specs if isinstance(specs, list) else [specs]:
        if not isinstance(spec, Mapping):
            continue
        is_list = "listprice" in str(spec.get("priceType", "")).lower().replace("_", "")
        if is_list == list_price and (price := to_price(spec.get("price"))) is not None:
            return price
    return None


def _size_from_properties(node: Mapping[str, Any]) -> str | None:
    props = node.get("additionalProperty")
    for prop in props if isinstance(props, list) else [props]:
        if isinstance(prop, Mapping) and "size" in str(prop.get("name", "")).lower():
            if (size := eu_size(prop.get("value"))) is not None:
                return size
    return None


# --- JSON embebido (fallback) -------------------------------------------------

def _embedded_units(html: str) -> Iterator[_Raw]:
    for block in _JSON_SCRIPT_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            size = _first_size(node)
            if size is None:
                continue
            price = _first_price(node, _PRICE_KEYS)
            if price is None:
                continue
            in_stock = _unit_in_stock(node)
            if in_stock is None:
                continue  # sin señal de stock: no es una unidad vendible fiable
            original = _first_price(node, _ORIGINAL_PRICE_KEYS)
            yield size, price, original, in_stock


def _unit_in_stock(node: Mapping[str, Any]) -> bool | None:
    for key in _STOCK_BOOL_KEYS:
        if isinstance(value := node.get(key), bool):
            return value
    for key in _STOCK_QTY_KEYS:
        value = node.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value > 0
        if isinstance(value, str) and (signal := availability_in_stock(value)) is not None:
            return signal
    for key in ("availability", "status", "stockStatus"):
        if (signal := availability_in_stock(node.get(key))) is not None:
            return signal
    return None


# --- utilidades ---------------------------------------------------------------

def _walk(data: Any) -> Iterator[Mapping[str, Any]]:
    stack: list[Any] = [data]
    visited = 0
    while stack and visited < _MAX_NODES:
        current = stack.pop()
        visited += 1
        if isinstance(current, Mapping):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _is_type(node: Mapping[str, Any], name: str) -> bool:
    kind = node.get("@type")
    return kind == name or (isinstance(kind, list) and name in kind)


def _first_size(node: Mapping[str, Any]) -> str | None:
    for key in _SIZE_KEYS:
        value = node.get(key)
        if isinstance(value, Mapping):
            value = value.get("value") or value.get("label") or value.get("name")
        if (size := eu_size(value)) is not None:
            return size
    return None


def _first_price(node: Mapping[str, Any], keys: Sequence[str]) -> Decimal | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, Mapping):  # {"amount": 8995} en céntimos o {"value": 89.95}
            if isinstance(cents := value.get("amount"), int) and not isinstance(cents, bool):
                value = Decimal(cents) / 100
            else:
                value = value.get("value") or value.get("formatted") or value.get("amount")
        if (price := to_price(value)) is not None:
            return price
    return None


def _better(candidate: ScrapedOffer, current: ScrapedOffer) -> bool:
    if candidate.in_stock != current.in_stock:
        return candidate.in_stock
    return candidate.price < current.price


def _is_zalando_host(host: str) -> bool:
    return host == "zalando.es" or host.endswith(".zalando.es")


def _product_url(html: str, page_url: str | None, sku: str) -> str:
    candidates: list[str] = []
    if canonical := _CANONICAL_RE.search(html):
        candidates.append(canonical.group(1))
    if page_url:
        candidates.append(page_url)
    for candidate in candidates:
        absolute = urljoin(_BASE_URL, candidate)
        parts = urlsplit(absolute)
        if parts.scheme == "https" and _is_zalando_host(parts.hostname or ""):
            return absolute
    return f"{_BASE_URL}/catalogo/?q={quote(sku)}"


# -----------------------------------------------------------------------------
# CLI de diagnóstico
# -----------------------------------------------------------------------------

async def _probe(sku: str, url: str | None) -> int:
    scraper = ZalandoScraper(product_urls={sku: url} if url else KNOWN_PDP_URLS)
    print(f"Zalando · {sku} · origen: {url or 'búsqueda'}", file=sys.stderr)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            offers = await scraper.fetch_offers(client, ProductRef(sku=sku, brand="", name=sku))
    except ScraperError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for offer in offers:
        original = f" (antes {offer.original_price} €)" if offer.original_price else ""
        print(f"EU {offer.size:>5}  {offer.price:>8} €{original}  {'stock' if offer.in_stock else 'agotado':<8} "
              f"{offer.affiliate_url}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prueba en vivo del scraper Zalando")
    parser.add_argument("sku")
    parser.add_argument("--url", help="URL exacta de la PDP")
    args = parser.parse_args()
    sys.exit(asyncio.run(_probe(args.sku.upper(), args.url)))
