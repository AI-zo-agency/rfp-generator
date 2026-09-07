"""Final checks must not run when it is switched off — on EVERY path.

Regression: the flag was checked at the generator tail and the API endpoint,
but Celery's _PHASE_DISPATCH maps "build-finalize" straight to
run_build_finalize_pass, so the phase chain (phase-4-review -> build-finalize)
reached it without passing either gate and Final checks ran anyway.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services import proposal_fulfill_rfp_gaps as M


class BuildFinalizeGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_flag_skips_the_work(self):
        with (
            patch("app.core.config.settings.build_finalize_enabled", False),
            patch.object(M, "run_fulfill_rfp_gaps", new=AsyncMock()) as run,
        ):
            await M.run_build_finalize_pass("r1")
        run.assert_not_awaited()

    async def test_enabled_flag_still_runs_it(self):
        with (
            patch("app.core.config.settings.build_finalize_enabled", True),
            patch.object(M, "run_fulfill_rfp_gaps", new=AsyncMock()) as run,
        ):
            await M.run_build_finalize_pass("r1")
        run.assert_awaited_once()
        self.assertEqual(run.await_args.kwargs.get("mode"), "build_finalize")

    def test_celery_dispatch_points_at_the_gated_function(self):
        # If this mapping ever changes, the gate above must move with it.
        from app.celery_app import _PHASE_DISPATCH

        self.assertEqual(
            _PHASE_DISPATCH["build-finalize"],
            ("app.services.proposal_fulfill_rfp_gaps", "run_build_finalize_pass"),
        )


if __name__ == "__main__":
    unittest.main()
