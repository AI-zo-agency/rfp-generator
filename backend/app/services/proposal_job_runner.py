"""Track long-running proposal jobs so lightweight endpoints (auth, health) stay responsive.

Two backends, selected by settings.celery_enabled (i.e. whether REDIS_URL is
set):

- Celery/Redis (production): the actual work runs in a separate worker
  process, dispatched via a `celery_dispatch` callable the caller provides.
  A Redis key holds the {job_type, celery_task_id, started_at} lock; job
  status is derived live from Celery's own AsyncResult state. This is what
  makes a job survive a web-process restart instead of being silently
  orphaned.
- In-memory asyncio (local dev without Redis): unchanged from before Celery
  existed — `asyncio.create_task` in this same process, tracked in the
  `_jobs`/`_tasks` dicts below.

Callers that don't pass `celery_dispatch` always get the in-memory path,
Celery or not — this lets call sites opt in individually.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from app.core.config import settings

logger = logging.getLogger(__name__)

JobStatus = Literal["running", "completed", "failed", "cancelled"]

_REDIS_KEY_PREFIX = "zo:job:"
_REDIS_LOCK_TTL_SEC = 3600  # matches celery_app.py's task_time_limit


@dataclass
class ProposalJobRecord:
    rfp_id: str
    job_type: str
    status: JobStatus = "running"
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    result: Any = None
    celery_task_id: str | None = None


_jobs: dict[str, ProposalJobRecord] = {}
_tasks: dict[str, asyncio.Task[None]] = {}
_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis_key(lock_key: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{lock_key}"


async def _redis_get_job(rfp_id: str, lock_key: str) -> ProposalJobRecord | None:
    import json

    from app.services.redis_client import get_redis

    raw = await get_redis().get(_redis_key(lock_key))
    if not raw:
        return None
    data = json.loads(raw)
    task_id = data.get("celery_task_id")
    record = ProposalJobRecord(
        rfp_id=rfp_id,
        job_type=data.get("job_type", ""),
        started_at=data.get("started_at", _now()),
        celery_task_id=task_id,
    )
    if not task_id:
        return record

    from celery.result import AsyncResult

    from app.celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    state = result.state
    if state in ("PENDING", "STARTED", "RETRY"):
        record.status = "running"
    elif state == "SUCCESS":
        record.status = "completed"
    elif state == "REVOKED":
        record.status = "cancelled"
    else:  # FAILURE and anything unexpected
        record.status = "failed"
        record.error = str(result.result)[:2000] if result.result else state

    date_done = getattr(result, "date_done", None)
    if date_done is not None:
        record.finished_at = date_done.isoformat()
    return record


async def get_proposal_job(rfp_id: str, *, lock_key: str | None = None) -> ProposalJobRecord | None:
    key = lock_key or rfp_id
    if settings.celery_enabled:
        record = await _redis_get_job(rfp_id, key)
        if record is not None and record.status != "running":
            # Job finished — drop the lock so the next start isn't blocked.
            await _redis_clear_job(key)
        return record
    async with _lock:
        return _jobs.get(key)


async def _redis_clear_job(lock_key: str) -> None:
    from app.services.redis_client import get_redis

    await get_redis().delete(_redis_key(lock_key))


async def is_proposal_job_running(rfp_id: str, *, lock_key: str | None = None) -> bool:
    job = await get_proposal_job(rfp_id, lock_key=lock_key)
    return job is not None and job.status == "running"


async def start_proposal_job(
    rfp_id: str,
    job_type: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    replace: bool = False,
    celery_dispatch: Callable[[], Any] | None = None,
    lock_key: str | None = None,
) -> ProposalJobRecord:
    """Run a long proposal coroutine in the background; returns immediately.

    celery_dispatch, if given and settings.celery_enabled, is called to
    enqueue the Celery task — it must return an object with a `.id`
    attribute (e.g. an AsyncResult from `some_task.delay(...)`). Without it
    (or without Redis configured), falls back to the in-memory asyncio path.

    lock_key scopes the "one job at a time" lock — defaults to rfp_id (the
    existing behavior: any one of the 7 proposal-pipeline phases blocks any
    other on the same RFP). Pass a distinct key (e.g. f"{rfp_id}:go-no-go")
    for a workflow that must NOT contend with the proposal pipeline's lock —
    Go/No-Go and proposal generation have always been independent, tracked
    in separate dicts before this migration; lock_key preserves that.
    """
    key = lock_key or rfp_id
    if settings.celery_enabled and celery_dispatch is not None:
        return await _start_celery_job(
            rfp_id, job_type, celery_dispatch, replace=replace, lock_key=key
        )
    return await _start_inmemory_job(
        rfp_id, job_type, coro_factory, replace=replace, lock_key=key
    )


async def _start_celery_job(
    rfp_id: str,
    job_type: str,
    celery_dispatch: Callable[[], Any],
    *,
    replace: bool,
    lock_key: str,
) -> ProposalJobRecord:
    import json

    from app.services.redis_client import get_redis

    existing = await get_proposal_job(rfp_id, lock_key=lock_key)
    if existing and existing.status == "running" and not replace:
        return existing

    started_at = _now()
    async_result = celery_dispatch()
    task_id = async_result.id
    record = ProposalJobRecord(
        rfp_id=rfp_id,
        job_type=job_type,
        started_at=started_at,
        celery_task_id=task_id,
    )
    await get_redis().set(
        _redis_key(lock_key),
        json.dumps(
            {"job_type": job_type, "celery_task_id": task_id, "started_at": started_at}
        ),
        ex=_REDIS_LOCK_TTL_SEC,
    )
    logger.info("Proposal job %s:%s dispatched to Celery task=%s", rfp_id, job_type, task_id)
    return record


async def _start_inmemory_job(
    rfp_id: str,
    job_type: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    replace: bool,
    lock_key: str,
) -> ProposalJobRecord:
    async with _lock:
        existing = _jobs.get(lock_key)
        if existing and existing.status == "running" and not replace:
            return existing
        record = ProposalJobRecord(rfp_id=rfp_id, job_type=job_type)
        _jobs[lock_key] = record

    async def _runner() -> None:
        try:
            record.result = await coro_factory()
            record.status = "completed"
            record.finished_at = _now()
            logger.info("Proposal job %s:%s completed", rfp_id, job_type)
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.finished_at = _now()
            logger.warning("Proposal job %s:%s cancelled", rfp_id, job_type)
            raise
        except Exception as exc:
            from app.services.proposal_generation_cancel import (
                ProposalGenerationCancelled,
            )

            if isinstance(exc, ProposalGenerationCancelled) or getattr(
                exc, "status_code", None
            ) == 409:
                record.status = "cancelled"
                record.error = str(exc)[:2000]
                record.finished_at = _now()
                logger.warning("Proposal job %s:%s stopped", rfp_id, job_type)
                return
            record.status = "failed"
            record.error = str(exc)[:2000]
            record.finished_at = _now()
            logger.exception("Proposal job %s:%s failed", rfp_id, job_type)

    task = asyncio.create_task(_runner(), name=f"proposal-job:{lock_key}:{job_type}")
    async with _lock:
        prior = _tasks.get(lock_key)
        if prior and not prior.done():
            prior.cancel()
        _tasks[lock_key] = task
    return record


async def cancel_proposal_job(rfp_id: str, *, lock_key: str | None = None) -> bool:
    key = lock_key or rfp_id
    if settings.celery_enabled:
        record = await _redis_get_job(rfp_id, key)
        if not record or record.status != "running" or not record.celery_task_id:
            return False
        from app.celery_app import celery_app

        celery_app.control.revoke(record.celery_task_id, terminate=True)
        await _redis_clear_job(key)
        return True
    async with _lock:
        task = _tasks.get(key)
        record = _jobs.get(key)
    if not task or not record or record.status != "running":
        return False
    task.cancel()
    return True


def proposal_job_to_dict(record: ProposalJobRecord | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "rfpId": record.rfp_id,
        "jobType": record.job_type,
        "status": record.status,
        "error": record.error,
        "startedAt": record.started_at,
        "finishedAt": record.finished_at,
    }
