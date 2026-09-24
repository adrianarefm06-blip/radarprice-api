"""Actualización mínima de esquema para BDs SQLite creadas con versiones previas.

`create_all` no altera tablas existentes: aquí se añaden las columnas nuevas
(siempre nullable o con default de servidor, sin reescribir datos).
"""
from sqlalchemy import Connection, inspect, text

from app.db.base import Base


def add_missing_columns(conn: Connection) -> list[str]:
    """Devuelve `tabla.columna` añadidas. Idempotente."""
    inspector = inspect(conn)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(conn.dialect)}'
            if column.server_default is not None:
                ddl += f" DEFAULT '{column.server_default.arg}'"  # type: ignore[attr-defined]
            conn.execute(text(ddl))
            added.append(f"{table.name}.{column.name}")
    return added
