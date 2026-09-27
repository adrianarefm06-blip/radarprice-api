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
    if response.status_code >= 400 and len(body) < 2000:
        print(f"  cuerpo: {body}")
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


# Tiendas candidatas a segunda fuente real. Shopify expone /products.json y
# /search/suggest.json públicos: mucho más estable que parsear HTML.
_CANDIDATES = (
    "www.adidas.es", "www.newbalance.es", "www.footlocker.es", "www.jdsports.es", "www.snipes.es",
    "www.courir.es", "www.footdistrict.com", "www.sivasdescalzo.com", "www.bstn.com", "www.afew-store.com",
    "www.43einhalb.com", "www.overkillshop.com", "www.solebox.com", "www.basket4ballers.com",
    "www.kickgame.co.uk", "www.naked-copenhagen.com", "www.hanon-shop.com", "www.titolo.ch",
    "www.forum-sport.com", "www.deporvillage.com", "www.sprintersports.com", "www.atmosferasport.es",
    "www.urbanjunglestore.com", "www.einhalb.com", "www.kithe.eu", "eu.kith.com", "www.endclothing.com",
    "www.size.co.uk", "www.asphaltgold.com", "www.allikestore.com",
)


async def _status(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> tuple[str, str]:
    started = time.monotonic()
    try:
        response = await client.get(url, headers=headers, timeout=12)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}", f"{time.monotonic() - started:.1f}s"
    return f"HTTP {response.status_code} {len(response.content)}B", response.text[:120].replace("\n", " ")


async def candidates(client: httpx.AsyncClient, skus: list[str]) -> None:
    html = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "es-ES,es;q=0.9"}
    api = {"User-Agent": _UA, "Accept": "application/json"}
    print("\n=== Tiendas candidatas ===")
    for host in _CANDIDATES:
        home, _ = await _status(client, f"https://{host}/", html)
        feed, head = await _status(client, f"https://{host}/products.json?limit=1", api)
        shopify = '"products"' in head
        print(f"{host:<28} home={home:<22} products.json={feed:<22} shopify={shopify}")
        if shopify:
            for sku in skus:
                found, body = await _status(
                    client, f"https://{host}/search/suggest.json?q={quote(sku)}&resources[type]=product", api)
                print(f"    suggest {sku}: {found} {body}")
    for sku in ("HQ8708", "B75806"):
        for path in (f"/api/products/{sku}", f"/api/products/{sku}/availability"):
            result, body = await _status(client, f"https://www.adidas.es{path}", api)
            print(f"adidas {path}: {result} {body}")


async def zalando_http2(sku: str) -> None:
    headers = {
        "User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9", "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
    print("\n=== Zalando con HTTP/2 ===")
    try:
        async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=15) as client:
            await _raw(client, "Zalando búsqueda (h2)", f"https://www.zalando.es/catalogo/?q={quote(sku)}", sku, headers)
    except ImportError as exc:
        print(f"  sin soporte h2: {exc}")


def nike_wall_products(html: str) -> list[tuple[str, str, str]]:
    """(código, título, url) de los productos del __NEXT_DATA__ de una búsqueda de Nike."""
    from app.scrapers.nike import extract_next_data

    found: dict[str, tuple[str, str, str]] = {}
    stack: list[object] = [extract_next_data(html)]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            code = node.get("productCode") or node.get("styleColor")
            if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{6}-\d{3}", code):
                copy = node.get("copy")
                title = copy.get("title") if isinstance(copy, dict) else node.get("title")
                url = node.get("pdpUrl") or node.get("url") or ""
                if isinstance(url, dict):
                    url = url.get("url", "")
                found.setdefault(code, (code, str(title), str(url)))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return list(found.values())


async def nike_search(client: httpx.AsyncClient, sku: str) -> None:
    html = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "es-ES,es;q=0.9"}
    response = await client.get(f"https://www.nike.com/es/w?q={quote(sku)}", headers=html)
    products = nike_wall_products(response.text)
    print(f"\n=== Nike búsqueda {sku}: HTTP {response.status_code}, {len(products)} productos ===")
    for code, title, url in products[:30]:
        mark = "  <== coincide" if code == sku else ""
        print(f"  {code}  {title[:50]:<50} {url}{mark}")


async def shopify(client: httpx.AsyncClient, host: str, sku: str) -> None:
    """Primer resultado de /search/suggest.json → variantes de /products/{handle}.js."""
    api = {"User-Agent": _UA, "Accept": "application/json"}
    print(f"\n=== Shopify {host} · {sku} ===")
    cart = await client.get(f"https://{host}/cart.js", headers=api)
    print(f"  moneda: {cart.json().get('currency') if cart.status_code == 200 else cart.status_code}")
    suggest = await client.get(f"https://{host}/search/suggest.json",
                               params={"q": sku, "resources[type]": "product", "resources[limit]": "3"}, headers=api)
    for item in suggest.json()["resources"]["results"]["products"]:
        print(f"  sugerido: {item.get('handle')} · {item.get('title')} · {item.get('price')} · {item.get('url')}")
        product = (await client.get(f"https://{host}/products/{item['handle']}.js", headers=api)).json()
        print(f"    options={[o.get('name') if isinstance(o, dict) else o for o in product.get('options', [])]} "
              f"tags={product.get('tags')} vendor={product.get('vendor')}")
        for variant in product.get("variants", [])[:30]:
            print(f"    {variant.get('title')!r:<22} sku={variant.get('sku')!r:<22} "
                  f"precio={variant.get('price')} antes={variant.get('compare_at_price')} "
                  f"disp={variant.get('available')}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", action="append", choices=sorted(SCRAPER_REGISTRY),
                        help="tienda a probar (repetible; por defecto todas)")
    parser.add_argument("--no-stores", action="store_true", help="omite la prueba de los scrapers")
    parser.add_argument("--candidates", action="store_true", help="sondea tiendas candidatas (Shopify, adidas)")
    parser.add_argument("--nike-search", action="append", default=[], metavar="SKU",
                        help="lista los productos que devuelve la búsqueda de Nike (repetible)")
    parser.add_argument("--shopify", action="append", default=[], metavar="HOST:SKU",
                        help="detalle de variantes de una tienda Shopify (repetible)")
    parser.add_argument("--zalando-h2", metavar="SKU", help="prueba Zalando con HTTP/2 y cabeceras de navegador")
    parser.add_argument("--diagnose", action="append", default=[], metavar="SKU",
                        help="volcado de peticiones crudas para este SKU (repetible)")
    args = parser.parse_args()
    # Mismo cliente que SyncService (HTTP/2).
    async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=20) as client:
        if not args.no_stores:
            for store in args.store or sorted(SCRAPER_REGISTRY):
                await probe_store(client, store)
        for sku in args.nike_search:
            await nike_search(client, sku)
        if args.candidates:
            await candidates(client, ["DD1503-101", "HQ8708", "CW2288-111"])
        for spec in args.shopify:
            host, _, sku = spec.partition(":")
            await shopify(client, host, sku)
        for sku in args.diagnose:
            await diagnose(client, sku)
    if args.zalando_h2:
        await zalando_http2(args.zalando_h2)


if __name__ == "__main__":
    asyncio.run(main())
