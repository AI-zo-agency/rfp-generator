from fastapi import APIRouter

from app.api.v1 import health, knowledge_base, proposals, rfps, sync_jobs
from app.financial.router import router as financials_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(rfps.router)
api_router.include_router(sync_jobs.router)
api_router.include_router(proposals.router)
api_router.include_router(proposals.proposals_direct_router)
api_router.include_router(knowledge_base.router)
api_router.include_router(financials_router)

