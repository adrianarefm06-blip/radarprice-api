from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RADARPRICE_", extra="ignore", frozen=True)

    database_url: str = "sqlite+aiosqlite:///./radarprice.db"
    sql_echo: bool = False
    seed_on_startup: bool = True
    sync_api_key: str | None = None
    cors_origins: tuple[str, ...] = ("*",)
    # Unión de segmentos de la app (niños 28-38, mujer 35-42, hombre 39-46).
    supported_sizes: tuple[str, ...] = (
        "28", "29", "30", "31", "32", "33", "34", "35", "35.5", "36", "36.5", "37", "37.5",
        "38", "38.5", "39", "40", "40.5", "41", "42", "42.5", "43", "44", "44.5", "45", "46",
    )
    scraper_concurrency: int = 4
    # Tiendas con scraper real (resto simuladas). Disponibles: SCRAPER_REGISTRY (Nike, Zalando).
    # Zalando es opt-in: RADARPRICE_REAL_SCRAPERS='["Nike","Zalando"]'. Desactivar todo: '[]'
    real_scrapers: tuple[str, ...] = ("Nike",)
    http_timeout_seconds: float = 10.0
    history_retention_days: int = 365


@lru_cache
def get_settings() -> Settings:
    return Settings()
