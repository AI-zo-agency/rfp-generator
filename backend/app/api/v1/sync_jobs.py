import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
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


class SyncJobTrigger(BaseModel):
    sync_mode: str = Field(alias="syncMode", default="today")
    sync_date: str | None = Field(alias="syncDate", default=None)
    tab: str = Field(default="all")

    model_config = {"populate_by_name": True}


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


VALID_TABS = {"all", "hot", "warm", "review"}

# Guards against a second sync being launched while one is still running.
# Each run drives a browser, so overlapping runs stack real Chromium processes.
_sync_lock = asyncio.Lock()
_sync_running = False


@router.post("/trigger")
async def trigger_sync_job(payload: SyncJobTrigger) -> dict[str, object]:
    global _sync_running

    logger = logging.getLogger(__name__)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    tab = (payload.tab or "all").lower()
    if tab not in VALID_TABS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tab '{payload.tab}'. Expected one of: {', '.join(sorted(VALID_TABS))}",
        )

    # "all" means every date. Keep it distinct from an unset date, which means
    # today — an empty string must not silently collapse into today.
    if payload.sync_mode == "all":
        target_date = ""
    else:
        target_date = (payload.sync_date or "").strip() or datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")

    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
        raise HTTPException(
            status_code=422,
            detail=f"syncDate must be YYYY-MM-DD, got '{target_date}'",
        )

    async with _sync_lock:
        if _sync_running:
            raise HTTPException(
                status_code=409,
                detail="A JustWin sync is already running. Wait for it to finish before starting another.",
            )
        _sync_running = True

    if sb.use_supabase_db():
        sb.create_sync_job(job_id)

    async def _run_playwright_sync():
        global _sync_running
        try:
            from app.services.justwin_sync import run_justwin_sync

            await asyncio.to_thread(run_justwin_sync, job_id, target_date, tab)
        except Exception as exc:
            logger.error("Failed to run JustWin Playwright sync: %s", exc)
            # Runner usually marks the job failed; cover the case where it
            # never got that far (e.g. import / browser binary missing).
            if sb.use_supabase_db():
                try:
                    running = sb.get_running_sync_job()
                    if running and running.get("id") == job_id:
                        sb.finish_sync_job(
                            job_id,
                            status="failed",
                            rfps_found=0,
                            pdfs_downloaded=0,
                            error=str(exc),
                        )
                except Exception:  # noqa: BLE001
                    sb.finish_sync_job(
                        job_id,
                        status="failed",
                        rfps_found=0,
                        pdfs_downloaded=0,
                        error=str(exc),
                    )
        finally:
            _sync_running = False

    asyncio.create_task(_run_playwright_sync())

    scope = target_date or "all dates"
    return {
        "ok": True,
        "jobId": job_id,
        "status": "running",
        "syncMode": payload.sync_mode,
        "syncDate": target_date,
        "tab": tab,
        "startedAt": now,
        "message": f"Started JustWin sync for {scope} ({tab} leads)",
    }


@router.post("", status_code=201)
def create_sync_job(payload: SyncJobCreate) -> dict[str, str]:
    if sb.use_supabase_db():
        sb.create_sync_job(payload.id)
    return {"ok": "true", "id": payload.id}
