from fastapi import APIRouter

from app.core.config import settings
from app.models.rfp import HealthResponse
from app.services.rfp_repository import init_db

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    init_db()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        database_path=str(settings.database_path),
    )
