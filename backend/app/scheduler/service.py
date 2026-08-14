"""Build the BlockingScheduler. Jobs live in jobs.py."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import Settings
from app.scheduler.jobs import JOBS
from app.scheduler.trigger import trigger_job

logger = logging.getLogger(__name__)


def first_run_time(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Immediate first fire when run-on-start is on; otherwise the cron trigger decides."""
    if not settings.scheduler_run_on_start:
        return None
    tz = ZoneInfo(settings.scheduler_timezone)
    current = now if now is not None else datetime.now(tz)
    if current.tzinfo is None:
        return current.replace(tzinfo=tz)
    return current.astimezone(tz)


def build_scheduler(settings: Settings) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    startup = first_run_time(settings)
    for job in JOBS:
        scheduler.add_job(
            trigger_job,
            trigger=CronTrigger.from_crontab(job.cron, timezone=job.timezone),
            id=job.id,
            kwargs={"job": job, "settings": settings},
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            replace_existing=True,
            next_run_time=startup,
        )
        logger.info(
            "operation=scheduler_register job_id=%s cron=%s timezone=%s path=%s "
            "run_on_start=%s",
            job.id,
            job.cron,
            job.timezone,
            job.path,
            settings.scheduler_run_on_start,
        )
    return scheduler
