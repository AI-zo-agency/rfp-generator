"""Named nightly jobs. Add a row here when a new platform gets a sync route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    path: str
    cron: str
    timezone: str
    method: str = "POST"
    body: dict[str, Any] | None = None
    timeout_seconds: float = 600


JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        id="quickbooks_nightly",
        path="/api/v1/financials/quickbooks/sync",
        cron="0 23 * * *",
        timezone="America/Los_Angeles",
        body={"mode": "auto"},
        timeout_seconds=600,
    ),
)


def job_by_id(job_id: str) -> ScheduledJob | None:
    for job in JOBS:
        if job.id == job_id:
            return job
    return None
