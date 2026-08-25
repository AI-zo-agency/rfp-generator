"""A Celery task killed with its worker stays STARTED forever — the job runner
must treat an over-age STARTED task as a dead zombie so it never blocks the RFP."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.proposal_job_runner import _ZOMBIE_STARTED_SEC, _iso_age_sec


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


class ZombieStartedDetectionTests(unittest.TestCase):
    def test_stale_started_task_is_over_zombie_threshold(self) -> None:
        # A 5-hour-old STARTED task (worker was killed) must read as dead.
        age = _iso_age_sec(_ago(hours=5))
        assert age is not None
        self.assertGreater(age, _ZOMBIE_STARTED_SEC)

    def test_fresh_running_task_is_under_zombie_threshold(self) -> None:
        # A task that started minutes ago is a real in-flight run — keep it.
        age = _iso_age_sec(_ago(minutes=3))
        assert age is not None
        self.assertLess(age, _ZOMBIE_STARTED_SEC)

    def test_threshold_exceeds_hard_time_limit(self) -> None:
        # Must be at least the Celery hard time limit, or a legitimately
        # long-running job could be culled while still alive.
        self.assertGreaterEqual(_ZOMBIE_STARTED_SEC, 3600)

    def test_bad_timestamp_returns_none(self) -> None:
        self.assertIsNone(_iso_age_sec(None))
        self.assertIsNone(_iso_age_sec("not-a-date"))


if __name__ == "__main__":
    unittest.main()
