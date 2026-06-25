from fastapi import APIRouter

from app.core.config import settings
from app.models.rfp import HealthResponse
from app.services import supabase_db as sb
from app.services.rfp_repository import init_db

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    init_db()
    if sb.use_supabase_db():
        return HealthResponse(
            status="ok",
            app=settings.app_name,
            database="supabase",
            databasePath=None,
        )
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        database="sqlite",
        databasePath=str(settings.database_path),
    )
