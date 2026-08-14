"""Always-on APScheduler worker: ``python -m app.scheduler``."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.scheduler.jobs import JOBS
from app.scheduler.service import build_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info(
        "operation=scheduler_start job_count=%s backend=%s timezone=%s "
        "run_on_start=%s note=sync_steps_log_on_api",
        len(JOBS),
        settings.scheduler_backend_url,
        settings.scheduler_timezone,
        settings.scheduler_run_on_start,
    )
    scheduler = build_scheduler(settings)
    scheduler.start()


if __name__ == "__main__":
    main()
