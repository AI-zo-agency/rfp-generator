"""Build the BlockingScheduler. Jobs live in jobs.py."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import Settings
from app.scheduler.jobs import JOBS, ScheduledJob
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


def _job_next_run_time(
    job: ScheduledJob,
    settings: Settings,
    *,
    startup: datetime | None,
) -> datetime | None:
    if job.run_on_start is False:
        return None
    if job.run_on_start is True:
        tz = ZoneInfo(settings.scheduler_timezone)
        return datetime.now(tz)
    return startup


def build_scheduler(settings: Settings) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    startup = first_run_time(settings)
    for job in JOBS:
        next_run = _job_next_run_time(job, settings, startup=startup)
        add_kwargs: dict = {
            "trigger": CronTrigger.from_crontab(job.cron, timezone=job.timezone),
            "id": job.id,
            "kwargs": {"job": job, "settings": settings},
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": 3600,
            "replace_existing": True,
        }
        # Explicit None pauses the job in APScheduler — omit so cron computes.
        if next_run is not None:
            add_kwargs["next_run_time"] = next_run
        scheduler.add_job(trigger_job, **add_kwargs)
        logger.info(
            "operation=scheduler_register job_id=%s cron=%s timezone=%s path=%s "
            "run_on_start=%s",
            job.id,
            job.cron,
            job.timezone,
            job.path,
            settings.scheduler_run_on_start
            if job.run_on_start is None
            else job.run_on_start,
        )
    return scheduler
