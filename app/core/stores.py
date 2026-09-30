from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping
from urllib.parse import quote_plus

@dataclass(frozen=True, slots=True)
class StoreInfo:
    name: str
    search_url_template: str
    is_official_retailer: bool

    @property
    def logo_url(self) -> str:
        # Sin logos alojados (el CDN previsto nunca existió): "" → la app pinta la inicial.
        return ""

    def affiliate_url(self, sku: str) -> str:
        return self.search_url_template.format(sku=quote_plus(sku))


STORES: Final[Mapping[str, StoreInfo]] = MappingProxyType({
    s.name: s
    for s in (
        StoreInfo("Nike", "https://www.nike.com/es/w?q={sku}", True),
        StoreInfo("adidas", "https://www.adidas.es/search?q={sku}", True),
        StoreInfo("Foot Locker", "https://www.footlocker.es/es/search?query={sku}", False),
        StoreInfo("Zalando", "https://www.zalando.es/catalogo/?q={sku}", False),
        StoreInfo("StockX", "https://stockx.com/es-es/search?s={sku}", False),
        # Solo con scraper real (Shopify): sin ofertas de demostración en el seed.
        StoreInfo("Urban Jungle", "https://www.urbanjunglestore.com/search?q={sku}", False),
        StoreInfo("Asphaltgold", "https://www.asphaltgold.com/search?q={sku}", False),
    )
})


def store_logo_url(store_name: str) -> str:
    store = STORES.get(store_name)
    return store.logo_url if store else ""
