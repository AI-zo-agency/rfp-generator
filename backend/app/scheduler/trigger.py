"""HTTP trigger for scheduler jobs. FastAPI remains the only Supabase writer."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.scheduler.jobs import ScheduledJob

logger = logging.getLogger(__name__)


def trigger_job(job: ScheduledJob, *, settings: Settings) -> dict[str, Any]:
    base = (settings.scheduler_backend_url or "").rstrip("/")
    secret = settings.quickbooks_cron_secret or ""
    if not base or not secret:
        logger.warning(
            "operation=scheduler_job job_id=%s step=config status=skipped "
            "reason=missing_config",
            job.id,
        )
        return {"status": "skipped", "job_id": job.id}

    url = f"{base}{job.path}"
    mode = (job.body or {}).get("mode", "")
    logger.info(
        "operation=scheduler_job job_id=%s step=http_request method=%s url=%s "
        "mode=%s timeout_s=%s note=waiting_on_api",
        job.id,
        job.method,
        url,
        mode,
        job.timeout_seconds,
    )
    started = time.monotonic()
    try:
        response = httpx.request(
            job.method,
            url,
            headers={
                "X-Cron-Secret": secret,
                "Content-Type": "application/json",
            },
            json=job.body or {},
            timeout=job.timeout_seconds,
        )
    except httpx.TimeoutException:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "operation=scheduler_job job_id=%s step=http_response "
            "status=timeout duration_ms=%s timeout_s=%s",
            job.id,
            duration_ms,
            job.timeout_seconds,
        )
        return {"status": "timeout", "job_id": job.id, "duration_ms": duration_ms}
    except httpx.ConnectError:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "operation=scheduler_job job_id=%s step=http_response "
            "status=connection_refused duration_ms=%s url=%s "
            "hint=start_the_api_process",
            job.id,
            duration_ms,
            url,
        )
        return {
            "status": "connection_refused",
            "job_id": job.id,
            "url": url,
            "duration_ms": duration_ms,
        }
    except httpx.HTTPError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "operation=scheduler_job job_id=%s step=http_response "
            "status=http_error duration_ms=%s error_type=%s",
            job.id,
            duration_ms,
            type(exc).__name__,
        )
        return {"status": "http_error", "job_id": job.id, "duration_ms": duration_ms}

    duration_ms = int((time.monotonic() - started) * 1000)
    if response.status_code == 409:
        logger.warning(
            "operation=scheduler_job job_id=%s step=http_response "
            "status=lease_held http_status=409 duration_ms=%s",
            job.id,
            duration_ms,
        )
        return {
            "status": "lease_held",
            "job_id": job.id,
            "http_status": 409,
            "duration_ms": duration_ms,
        }

    if response.status_code >= 400:
        logger.error(
            "operation=scheduler_job job_id=%s step=http_response "
            "status=http_error http_status=%s duration_ms=%s body=%s",
            job.id,
            response.status_code,
            duration_ms,
            (response.text or "")[:300],
        )
        return {
            "status": "http_error",
            "job_id": job.id,
            "http_status": response.status_code,
            "duration_ms": duration_ms,
        }

    logger.info(
        "operation=scheduler_job job_id=%s step=http_response status=success "
        "http_status=%s duration_ms=%s body=%s",
        job.id,
        response.status_code,
        duration_ms,
        (response.text or "")[:300],
    )
    return {
        "status": "success",
        "job_id": job.id,
        "http_status": response.status_code,
        "duration_ms": duration_ms,
    }
