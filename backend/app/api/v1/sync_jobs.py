from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import supabase_db as sb

router = APIRouter(prefix="/sync-jobs", tags=["sync-jobs"])


class SyncJobFinish(BaseModel):
    status: str
    rfps_found: int = Field(alias="rfpsFound", default=0)
    pdfs_downloaded: int = Field(alias="pdfsDownloaded", default=0)
    error: str | None = None

    model_config = {"populate_by_name": True}


class SyncJobCreate(BaseModel):
    id: str


@router.patch("/{job_id}")
def finish_sync_job(job_id: str, payload: SyncJobFinish) -> dict[str, str]:
    if sb.use_supabase_db():
        sb.finish_sync_job(
            job_id,
            status=payload.status,
            rfps_found=payload.rfps_found,
            pdfs_downloaded=payload.pdfs_downloaded,
            error=payload.error,
        )
    return {"ok": "true"}


@router.get("/latest")
def get_latest_sync_job() -> dict[str, object]:
    if not sb.use_supabase_db():
        return {"job": None}
    job = sb.get_latest_sync_job()
    return {"job": job}


@router.get("/running")
def get_running_sync_job() -> dict[str, object]:
    if not sb.use_supabase_db():
        return {"job": None}
    job = sb.get_running_sync_job()
    return {"job": job}


@router.post("", status_code=201)
def create_sync_job(payload: SyncJobCreate) -> dict[str, str]:
    if sb.use_supabase_db():
        sb.create_sync_job(payload.id)
    return {"ok": "true", "id": payload.id}
