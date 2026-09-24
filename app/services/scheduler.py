"""Sync periódico en el proceso de la API (asyncio, sin dependencias extra).

  lifespan ─▶ create_task(run_periodic_sync) ─▶ espera initial_delay ─▶ run ─▶ espera interval ─▶ run …
  shutdown ─▶ task.cancel()

Ningún fallo detiene el bucle: se registra y se reintenta en el siguiente ciclo.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Protocol

from app.core.errors import SyncAlreadyRunningError
from app.schemas.sync import SyncReportOut

_logger = logging.getLogger(__name__)


class _Runnable(Protocol):
    async def run(self) -> SyncReportOut: ...


async def run_periodic_sync(
    service: _Runnable,
    interval: timedelta,
    *,
    initial_delay: timedelta = timedelta(minutes=1),
) -> None:
    """Bucle infinito: cancelar la tarea para detenerlo."""
    if interval <= timedelta(0):
        raise ValueError("interval debe ser > 0")
    delay = initial_delay
    while True:
        await asyncio.sleep(max(delay.total_seconds(), 0))
        delay = interval
        try:
            report = await service.run()
        except SyncAlreadyRunningError:
            _logger.info("sync programado omitido: ya hay uno en curso")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — el planificador nunca debe morir por un sync fallido
            _logger.exception("sync programado falló; se reintentará en %s", interval)
        else:
            _logger.info(
                "sync programado: %d productos, %d ofertas, %d errores",
                report.products_processed, report.offers_upserted, len(report.errors),
            )
