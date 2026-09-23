from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping
from urllib.parse import quote_plus

_LOGO_CDN: Final = "https://cdn.radarprice.app/stores"


@dataclass(frozen=True, slots=True)
class StoreInfo:
    name: str
    logo_file: str
    search_url_template: str
    is_official_retailer: bool

    @property
    def logo_url(self) -> str:
        return f"{_LOGO_CDN}/{self.logo_file}"

    def affiliate_url(self, sku: str) -> str:
        return self.search_url_template.format(sku=quote_plus(sku))


STORES: Final[Mapping[str, StoreInfo]] = MappingProxyType({
    s.name: s
    for s in (
        StoreInfo("Nike", "nike.png", "https://www.nike.com/es/w?q={sku}", True),
        StoreInfo("adidas", "adidas.png", "https://www.adidas.es/search?q={sku}", True),
        StoreInfo("Foot Locker", "footlocker.png", "https://www.footlocker.es/es/search?query={sku}", False),
        StoreInfo("Zalando", "zalando.png", "https://www.zalando.es/catalogo/?q={sku}", False),
        StoreInfo("StockX", "stockx.png", "https://stockx.com/es-es/search?s={sku}", False),
    )
})


def store_logo_url(store_name: str) -> str:
    store = STORES.get(store_name)
    return store.logo_url if store else f"{_LOGO_CDN}/generic.png"
