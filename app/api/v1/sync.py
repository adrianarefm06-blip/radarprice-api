from fastapi import APIRouter, Depends

from app.api.deps import SyncServiceDep, require_sync_key
from app.schemas.sync import SyncReportOut

router = APIRouter(tags=["sync"])


@router.post(
    "/sync",
    response_model=SyncReportOut,
    dependencies=[Depends(require_sync_key)],
    summary="Sincronización manual de ofertas e histórico",
    responses={
        401: {"description": "API key inválida"},
        409: {"description": "Sync en curso"},
        503: {"description": "Sync deshabilitado (sin RADARPRICE_SYNC_API_KEY)"},
    },
)
async def run_sync(service: SyncServiceDep) -> SyncReportOut:
    return await service.run()
