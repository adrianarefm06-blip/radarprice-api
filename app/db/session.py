from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings


def create_engine_and_sessionmaker(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    is_sqlite = settings.database_url.startswith("sqlite")
    kwargs: dict[str, Any] = {"echo": settings.sql_echo}
    if is_sqlite and ":memory:" in settings.database_url:
        kwargs |= {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}

    engine = create_async_engine(settings.database_url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fks(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
