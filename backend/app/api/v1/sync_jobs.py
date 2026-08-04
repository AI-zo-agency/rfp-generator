import asyncio
import logging
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

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

    root_dir = Path(__file__).resolve().parents[4]
    frontend_dir = root_dir / "frontend"

    async def _run_playwright_sync():
        global _sync_running
        failure: str | None = None

        try:
            npx_bin = shutil.which("npx") or "/usr/local/bin/npx"
            npx_dir = os.path.dirname(npx_bin) if os.path.isabs(npx_bin) else ""
            env = os.environ.copy()
            env["PATH"] = f"{npx_dir}:/usr/local/bin:/opt/homebrew/bin:{env.get('PATH', '')}"
            # Headless by default so syncing does not steal focus with a browser
            # window. Set JUSTWIN_HEADLESS=false to watch a run.
            env["HEADLESS"] = os.environ.get("JUSTWIN_HEADLESS", "true")

            # "-" tells the script to accept every date.
            date_arg = target_date or "-"
            proc = await asyncio.create_subprocess_exec(
                npx_bin,
                "tsx",
                "scripts/justwin-sync/index.ts",
                job_id,
                date_arg,
                tab,
                cwd=str(frontend_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info(
                    "JustWin sync %s completed: %s",
                    job_id,
                    stdout.decode("utf-8", errors="ignore")[-500:],
                )
            else:
                failure = stderr.decode("utf-8", errors="ignore")[-500:]
                logger.warning(
                    "JustWin sync %s exited with code %s: %s",
                    job_id,
                    proc.returncode,
                    failure,
                )
        except Exception as exc:
            failure = str(exc)
            logger.error("Failed to run JustWin Playwright sync: %s", exc)
        finally:
            _sync_running = False

        # The script reports its own counts via PATCH /sync-jobs/{id} on success,
        # so only close the job here when it never got that far.
        if failure and sb.use_supabase_db():
            sb.finish_sync_job(
                job_id,
                status="failed",
                rfps_found=0,
                pdfs_downloaded=0,
                error=failure,
            )

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

