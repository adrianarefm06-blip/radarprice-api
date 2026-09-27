"""Prueba en vivo de los scrapers reales contra el catálogo semilla (sin base de datos).

Pensado para GitHub Actions (workflow "Sonda de scrapers"): las webs de las tiendas
suelen bloquear entornos de desarrollo, pero no los runners de GitHub.

  python -m scripts.probe_live                      # todas las tiendas reales × catálogo
  python -m scripts.probe_live --store Zalando      # una tienda
  python -m scripts.probe_live --diagnose DD1503-101  # además, volcado de peticiones crudas

Sale con 0 siempre: es un informe, no un test (las tiendas cambian sin avisar).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
from urllib.parse import quote

import httpx

from app.scrapers.base import ProductRef, ScraperError
from app.scrapers.registry import SCRAPER_REGISTRY
from app.seed.catalog import SEED_PRODUCTS

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
_NIKE_CHANNEL = "d9a5bc42-4b9c-4976-858a-f159cf99c647"


async def probe_store(client: httpx.AsyncClient, store: str) -> None:
    scraper = SCRAPER_REGISTRY[store]()
    print(f"\n=== {store} ===")
    for seed in SEED_PRODUCTS:
        product = ProductRef(sku=seed.sku, brand=seed.brand, name=seed.name)
        started = time.monotonic()
        try:
            offers = await scraper.fetch_offers(client, product)
        except ScraperError as exc:
            print(f"{seed.sku:<12} ERROR {type(exc).__name__}: {exc}")
            continue
        finally:
            elapsed = time.monotonic() - started
        if not offers:
            print(f"{seed.sku:<12} —  (no aplica a esta tienda)  {elapsed:.1f}s")
            continue
        in_stock = [o for o in offers if o.in_stock]
        low = min((o.price for o in in_stock), default=None)
        print(f"{seed.sku:<12} OK tallas={len(offers)} stock={len(in_stock)} desde={low} "
              f"url={offers[0].affiliate_url}  {elapsed:.1f}s")


async def _raw(client: httpx.AsyncClient, label: str, url: str, sku: str, headers: dict[str, str]) -> None:
    print(f"\n--- {label}\nGET {url}")
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        print(f"  error de red: {exc!r}")
        return
    body = response.text
    print(f"  HTTP {response.status_code} · final={response.url} · {len(body)} bytes · "
          f"content-type={response.headers.get('content-type')}")
    print(f"  __NEXT_DATA__={'__NEXT_DATA__' in body} · ld+json={body.count('application/ld+json')} · "
          f"menciones SKU={body.upper().count(sku.upper())}")
    hrefs = sorted(set(re.findall(r"https?://www\.[a-z]+\.[a-z]+/[^\"'\s<>]*" + re.escape(sku.split('-')[0]) +
                                  r"[^\"'\s<>]*", body, re.I)))[:5]
    for href in hrefs:
        print(f"  enlace: {href}")
    for match in list(re.finditer(re.escape(sku), body, re.I))[:3]:
        snippet = body[max(0, match.start() - 160): match.end() + 160].replace("\n", " ")
        print(f"  …{snippet}…")
    if not hrefs and response.status_code == 200:
        print(f"  inicio: {body[:300]!r}")


async def diagnose(client: httpx.AsyncClient, sku: str) -> None:
    html = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "es-ES,es;q=0.9"}
    api = {"User-Agent": _UA, "Accept": "application/json", "nike-api-caller-id": "com.nike.commerce.nikedotcom.web"}
    q = quote(sku)
    print(f"\n=== Diagnóstico {sku} ===")
    await _raw(client, "Nike búsqueda HTML", f"https://www.nike.com/es/w?q={q}", sku, html)
    await _raw(client, "Nike product_wall (API de la búsqueda)",
               "https://api.nike.com/discover/product_wall/v1/marketplace/ES/language/es/"
               f"consumerChannelId/{_NIKE_CHANNEL}?path=/es/w&queryType=PRODUCTS&searchTerms={q}&anchor=0&count=24",
               sku, api)
    await _raw(client, "Nike product_feed v2",
               "https://api.nike.com/product_feed/threads/v2/?filter=marketplace(ES)&filter=language(es)"
               f"&filter=channelId({_NIKE_CHANNEL})&filter=productInfo.merchProduct.styleColor({q})",
               sku, api)
    await _raw(client, "Zalando búsqueda HTML", f"https://www.zalando.es/catalogo/?q={q}", sku, html)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", action="append", choices=sorted(SCRAPER_REGISTRY),
                        help="tienda a probar (repetible; por defecto todas)")
    parser.add_argument("--diagnose", action="append", default=[], metavar="SKU",
                        help="volcado de peticiones crudas para este SKU (repetible)")
    args = parser.parse_args()
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        for store in args.store or sorted(SCRAPER_REGISTRY):
            await probe_store(client, store)
        for sku in args.diagnose:
            await diagnose(client, sku)


if __name__ == "__main__":
    asyncio.run(main())
