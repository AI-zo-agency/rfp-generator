"""Track long-running proposal jobs so lightweight endpoints (auth, health) stay responsive."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

JobStatus = Literal["running", "completed", "failed", "cancelled"]


@dataclass
class ProposalJobRecord:
    rfp_id: str
    job_type: str
    status: JobStatus = "running"
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    result: Any = None


_jobs: dict[str, ProposalJobRecord] = {}
_tasks: dict[str, asyncio.Task[None]] = {}
_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_proposal_job(rfp_id: str) -> ProposalJobRecord | None:
    async with _lock:
        return _jobs.get(rfp_id)


async def is_proposal_job_running(rfp_id: str) -> bool:
    job = await get_proposal_job(rfp_id)
    return job is not None and job.status == "running"


async def start_proposal_job(
    rfp_id: str,
    job_type: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    replace: bool = False,
) -> ProposalJobRecord:
    """Run a long proposal coroutine in the background; returns immediately."""
    async with _lock:
        existing = _jobs.get(rfp_id)
        if existing and existing.status == "running" and not replace:
            return existing
        record = ProposalJobRecord(rfp_id=rfp_id, job_type=job_type)
        _jobs[rfp_id] = record

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
            record.status = "failed"
            record.error = str(exc)[:2000]
            record.finished_at = _now()
            logger.exception("Proposal job %s:%s failed", rfp_id, job_type)

    task = asyncio.create_task(_runner(), name=f"proposal-job:{rfp_id}:{job_type}")
    async with _lock:
        prior = _tasks.get(rfp_id)
        if prior and not prior.done():
            prior.cancel()
        _tasks[rfp_id] = task
    return record


async def cancel_proposal_job(rfp_id: str) -> bool:
    async with _lock:
        task = _tasks.get(rfp_id)
        record = _jobs.get(rfp_id)
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
