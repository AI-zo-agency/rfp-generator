from fastapi import APIRouter, Depends

from app.api.auth_guard import require_user

from app.api.v1 import activity, analytics, brand_voice, health, knowledge_base, llm_cost, proposals, rfps, sync_jobs
from app.financial.router import router as financials_router
from app.leads.router import router as leads_router

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])
api_router.include_router(health.router)
api_router.include_router(activity.router)
api_router.include_router(analytics.router)
api_router.include_router(rfps.router)
api_router.include_router(llm_cost.router)
api_router.include_router(sync_jobs.router)
api_router.include_router(proposals.router)
api_router.include_router(proposals.proposals_direct_router)
api_router.include_router(knowledge_base.router)
api_router.include_router(financials_router)
api_router.include_router(leads_router)
api_router.include_router(brand_voice.router)

