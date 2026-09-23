from fastapi import APIRouter

from app.api.v1 import products, sync

api_router = APIRouter()
api_router.include_router(products.router)
api_router.include_router(sync.router)
