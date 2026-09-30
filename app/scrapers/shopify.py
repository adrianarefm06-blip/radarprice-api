"""Scraper genérico para tiendas Shopify (API pública de la tienda, sin HTML).

  GET /search/suggest.json?q={SKU} ──▶ hasta 5 productos candidatos
      └─▶ GET /products/{handle}.js ──▶ ¿el SKU del fabricante aparece en
          variantes, etiquetas, handle o descripción? ── no ─▶ siguiente candidato
          └─▶ variantes: talla EU (opción "Size"/"Talla"/"Größe"…) · precio · compare_at · available

- La moneda de la tienda se comprueba una vez (/cart.js): distinta de EUR → ScraperError.
- Tallas no EU (p. ej. solo US) o fuera de rango se descartan; si no queda ninguna → ScraperError.
- Tercios adidas ("42 2/3") → media talla más cercana; si colisionan gana la más barata en stock.
- Sin candidatos que coincidan → [] (la tienda no tiene ese modelo; no es un error).

Prueba en vivo:
  python -m app.scrapers.shopify www.urbanjunglestore.com HQ8708
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.scrapers.base import HttpScraper, ProductRef, ScrapedOffer, ScraperError
from app.scrapers.zalando import eu_size

_SIZE_OPTION_NAMES: Final = frozenset({"size", "talla", "größe", "groesse", "taglia", "taille", "eu size", "eu"})
_MAX_CANDIDATES: Final = 5
_CENTS: Final = Decimal("0.01")

_HEADERS: Final[Mapping[str, str]] = MappingProxyType({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
})


class ShopifyScraper(HttpScraper):
    default_headers = _HEADERS

    def __init__(
        self,
        store_name: str,
        host: str,
        *,
        currency: str = "EUR",
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        # Shopify limita por IP (429): ~1 petición cada 2 s por tienda.
        min_interval_seconds: float = 2.0,
    ) -> None:
        super().__init__(
            max_retries=max_retries, backoff_seconds=backoff_seconds, min_interval_seconds=min_interval_seconds,
        )
        self._store_name = store_name
        self._base = f"https://{host.strip('/')}"
        self._currency = currency
        self._currency_checked = False
        self._currency_lock = asyncio.Lock()

    @property
    def store_name(self) -> str:
        return self._store_name

    def build_url(self, product: ProductRef) -> str | None:
        return f"{self._base}/search/suggest.json"

    def parse(self, body: str, product: ProductRef) -> Sequence[ScrapedOffer]:  # pragma: no cover - no se usa
        raise NotImplementedError("ShopifyScraper trabaja con JSON: ver fetch_offers")

    async def fetch_offers(self, client: httpx.AsyncClient, product: ProductRef) -> Sequence[ScrapedOffer]:
        await self._check_currency(client)
        query = httpx.QueryParams({
            "q": product.sku, "resources[type]": "product", "resources[limit]": str(_MAX_CANDIDATES),
        })
        suggest = await self._get_json(client, f"{self._base}/search/suggest.json?{query}")
        for handle in candidate_handles(suggest):
            data = await self._get_json(client, f"{self._base}/products/{handle}.js")
            if isinstance(data, dict) and product_matches_sku(data, product.sku):
                return parse_product(data, url=f"{self._base}/products/{handle}", store=self._store_name)
        return []

    async def _check_currency(self, client: httpx.AsyncClient) -> None:
        if self._currency_checked:
            return
        async with self._currency_lock:
            if self._currency_checked:
                return
            cart = await self._get_json(client, f"{self._base}/cart.js")
            currency = cart.get("currency") if isinstance(cart, dict) else None
            if currency != self._currency:
                raise ScraperError(f"{self._store_name}: moneda {currency!r}, se esperaba {self._currency}")
            self._currency_checked = True

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Any:
        response = await self._get(client, url)
        try:
            return response.json()
        except ValueError as exc:
            raise ScraperError(f"{self._store_name}: respuesta no JSON en {url}") from exc


def candidate_handles(suggest: Any) -> list[str]:
    try:
        products = suggest["resources"]["results"]["products"]
    except (KeyError, TypeError) as exc:
        raise ScraperError(f"Shopify: formato de suggest desconocido ({exc!r})") from exc
    handles = [p.get("handle") for p in products if isinstance(p, dict)]
    return [h for h in handles if isinstance(h, str) and re.fullmatch(r"[\w-]+", h)][:_MAX_CANDIDATES]


def sku_pattern(sku: str) -> re.Pattern[str] | None:
    """'CW2288-111' → casa 'CW2288-111', 'CW2288 111', 'cw2288_111-42', 'HQ8708_8' (para 'HQ8708')…
    pero no 'CW2288-1119' ni 'XCW2288-111'. SKU demasiado corto → None (riesgo de falsos positivos)."""
    chunks = re.findall(r"[A-Za-z0-9]+", sku)
    if sum(len(c) for c in chunks) < 5:
        return None
    body = r"[\s_-]?".join(re.escape(c) for c in chunks)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![0-9])", re.I)


def product_matches_sku(product: Mapping[str, Any], sku: str) -> bool:
    """SKU del fabricante en variantes, etiquetas, handle o descripción del producto."""
    pattern = sku_pattern(sku)
    if pattern is None:
        return False
    fields: list[str] = [str(product.get("handle") or ""), str(product.get("description") or "")]
    tags = product.get("tags")
    if isinstance(tags, list):
        fields.extend(str(t) for t in tags)
    elif isinstance(tags, str):
        fields.append(tags)
    fields.extend(str(v.get("sku") or "") for v in product.get("variants") or [] if isinstance(v, dict))
    return any(pattern.search(field) for field in fields)


def _size_option_index(product: Mapping[str, Any]) -> int:
    for index, option in enumerate(product.get("options") or []):
        name = option.get("name") if isinstance(option, dict) else option
        if isinstance(name, str) and name.strip().lower() in _SIZE_OPTION_NAMES:
            return index
    return 0


def _cents(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        price = (Decimal(str(value)) * _CENTS).quantize(_CENTS)
    except ArithmeticError:
        return None
    return price if price > 0 else None


def featured_image(product: Mapping[str, Any], *, width: int = 800) -> str | None:
    """Foto principal del CDN de Shopify, redimensionada para móvil. Sin foto → None."""
    raw = product.get("featured_image")
    if not isinstance(raw, str) or not raw.strip():
        return None
    url = "https:" + raw if raw.startswith("//") else raw
    if not url.startswith("https://"):
        return None
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "width"] + [("width", str(width))]
    return urlunsplit(parts._replace(query=urlencode(query)))


def parse_product(product: Mapping[str, Any], *, url: str, store: str) -> list[ScrapedOffer]:
    index = _size_option_index(product)
    image = featured_image(product)
    by_size: dict[str, ScrapedOffer] = {}
    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        options = variant.get("options")
        label = options[index] if isinstance(options, list) and len(options) > index else variant.get("title")
        size = eu_size(label)
        price = _cents(variant.get("price"))
        if size is None or price is None:
            continue
        original = _cents(variant.get("compare_at_price"))
        offer = ScrapedOffer(
            size=size,
            price=price,
            in_stock=variant.get("available") is True,  # sin señal → agotado
            affiliate_url=url,
            original_price=original if original is not None and original > price else None,
            image_url=image,
        )
        current = by_size.get(size)
        if current is None or _better(offer, current):
            by_size[size] = offer
    if not by_size:
        raise ScraperError(f"{store}: el producto no tiene tallas EU reconocibles ({url})")
    return sorted(by_size.values(), key=lambda o: float(o.size))


def _better(candidate: ScrapedOffer, current: ScrapedOffer) -> bool:
    if candidate.in_stock != current.in_stock:
        return candidate.in_stock
    return candidate.price < current.price


# -----------------------------------------------------------------------------
# CLI de diagnóstico
# -----------------------------------------------------------------------------

async def _probe(host: str, sku: str) -> int:
    scraper = ShopifyScraper(host, host)
    try:
        async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=15) as client:
            offers = await scraper.fetch_offers(client, ProductRef(sku=sku, brand="", name=sku))
    except ScraperError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not offers:
        print("Sin coincidencias para ese SKU", file=sys.stderr)
        return 1
    for offer in offers:
        before = f" (antes {offer.original_price})" if offer.original_price else ""
        print(f"EU {offer.size:>5}  {offer.price:>8} €{before}  {'stock' if offer.in_stock else 'agotado':<8}")
    print(offers[0].affiliate_url)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prueba en vivo de una tienda Shopify")
    parser.add_argument("host")
    parser.add_argument("sku")
    args = parser.parse_args()
    sys.exit(asyncio.run(_probe(args.host, args.sku)))
