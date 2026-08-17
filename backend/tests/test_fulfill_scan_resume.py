"""Complete & clean stop/resume: pick up the saved step, never drop the report tail."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalPipelineCheckpoint, ProposalResearchCache
from app.services.proposal_pipeline_checkpoint import (
    fulfill_resume_step,
    record_generation_stopped,
)


def _cp(**kw) -> ProposalPipelineCheckpoint:
    return ProposalPipelineCheckpoint(updatedAt="2026-08-13T00:00:00Z", **kw)


def _research(cp: ProposalPipelineCheckpoint | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-08-13T00:00:00Z",
        pipelineCheckpoint=cp,
    )


class FulfillResumeStepTests(unittest.TestCase):
    def test_fresh_cache_starts_at_step_one(self):
        self.assertEqual(fulfill_resume_step(None), 1)
        self.assertEqual(fulfill_resume_step(_research(None)), 1)

    def test_resume_pointer_wins(self):
        cp = _cp(resumeFulfillStep=17, lastCompletedFulfillStep=16, stepIndex=17)
        self.assertEqual(fulfill_resume_step(_research(cp)), 17)

    def test_in_progress_scan_uses_live_step(self):
        cp = _cp(inProgressPhase="fulfill-scan", stepIndex=12)
        self.assertEqual(fulfill_resume_step(_research(cp)), 12)

    def test_completed_step_resumes_at_the_next_one(self):
        cp = _cp(lastCompletedFulfillStep=16)
        self.assertEqual(fulfill_resume_step(_research(cp)), 17)


class StopPreservesScanStepTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_during_scan_keeps_resume_step(self):
        prior = _research(
            _cp(
                inProgressPhase="fulfill-scan",
                stepIndex=17,
                stepTotal=19,
                lastCompletedFulfillStep=16,
                activityLabel="Scan RFP: review & quality gate",
                lastCompletedPhase="phase-4-review",
            )
        )
        saved: list[ProposalResearchCache] = []

        async def _save(updated):
            saved.append(updated)

        with (
            patch(
                "app.services.proposal_pipeline_checkpoint.aget_research_cache",
                new=AsyncMock(return_value=prior),
            ),
            patch(
                "app.services.proposal_pipeline_checkpoint.asave_research_cache",
                new=AsyncMock(side_effect=_save),
            ),
        ):
            await record_generation_stopped("r1", "fulfill-scan")

        self.assertTrue(saved)
        cp = saved[-1].pipeline_checkpoint
        assert cp is not None
        self.assertEqual(cp.resume_fulfill_step, 17)
        self.assertEqual(cp.step_index, 17)
        self.assertIsNone(cp.in_progress_phase)
        self.assertIn("Complete & clean", cp.last_error or "")


class StartPreservesFulfillResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_starting_scan_does_not_wipe_resume_step(self):
        from app.services.proposal_pipeline_checkpoint import record_phase_started

        prior = _research(
            _cp(
                resumeFulfillStep=17,
                lastCompletedFulfillStep=16,
                stepIndex=17,
                stepTotal=19,
                activityLabel="Scan RFP: review & quality gate",
                lastCompletedPhase="phase-4-review",
            )
        )
        saved: list[ProposalResearchCache] = []

        async def _save(updated):
            saved.append(updated)

        with (
            patch(
                "app.services.proposal_pipeline_checkpoint.aget_research_cache",
                new=AsyncMock(return_value=prior),
            ),
            patch(
                "app.services.proposal_pipeline_checkpoint.asave_research_cache",
                new=AsyncMock(side_effect=_save),
            ),
            patch(
                "app.services.proposal_pipeline_checkpoint.clear_stale_in_progress_checkpoint",
                new=AsyncMock(return_value=False),
            ),
        ):
            await record_phase_started("r1", "fulfill-scan")

        self.assertTrue(saved)
        cp = saved[-1].pipeline_checkpoint
        assert cp is not None
        self.assertEqual(cp.resume_fulfill_step, 17)
        self.assertEqual(cp.last_completed_fulfill_step, 16)
        self.assertEqual(cp.step_index, 17)
        self.assertEqual(fulfill_resume_step(saved[-1]), 17)


class ScanDoesNotSkipTheReportTailTests(unittest.TestCase):
    def test_quality_gate_and_report_stages_are_never_skipped(self):
        from app.services import proposal_fulfill_rfp_gaps as mod

        src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
        self.assertIn("step < resume_at and step < 17", src)
        self.assertIn("complete_fulfill_scan", src)


class StaleStopFlagTests(unittest.TestCase):
    def test_clear_allows_a_new_run_after_stop(self):
        from app.services.proposal_generation_cancel import (
            clear_generation_cancel,
            is_generation_cancelled,
            request_generation_cancel,
        )

        request_generation_cancel("rfp-stale-stop")
        self.assertTrue(is_generation_cancelled("rfp-stale-stop"))
        clear_generation_cancel("rfp-stale-stop")
        self.assertFalse(is_generation_cancelled("rfp-stale-stop"))


class FulfillHttpEnqueueTests(unittest.TestCase):
    def test_endpoint_returns_immediately_and_does_not_cancel_on_disconnect(self):
        from app.api.v1 import proposals as proposals_mod

        src = inspect.getsource(proposals_mod.fulfill_rfp_gaps_endpoint)
        self.assertIn("_enqueue_pipeline_phase", src)
        self.assertIn("fulfill-scan", src)
        self.assertNotIn("cancel_generation_on_disconnect", src)

    def test_enqueue_clears_stale_stop_flag_before_starting(self):
        from app.api.v1 import proposals as proposals_mod

        src = inspect.getsource(proposals_mod._enqueue_pipeline_phase)
        self.assertIn("clear_generation_cancel", src)

    def test_completing_fulfill_scan_does_not_reset_pipeline_resume(self):
        from app.services.proposal_pipeline_checkpoint import record_phase_completed

        src = inspect.getsource(record_phase_completed)
        self.assertIn('phase == "fulfill-scan"', src)
        self.assertIn("complete_fulfill_scan", src)


class QualityGateAccuracyFloorTests(unittest.TestCase):
    def test_three_rounds_remain_the_default(self):
        from app.services.proposal_quality_gate import MAX_ROUNDS, _configured_max_rounds

        self.assertEqual(MAX_ROUNDS, 3)
        self.assertEqual(_configured_max_rounds(), 3)

    def test_run_quality_gate_still_requires_all_three_acts(self):
        from app.services.proposal_quality_gate import run_quality_gate

        params = inspect.signature(run_quality_gate).parameters
        self.assertNotIn("verify_claims", params)


if __name__ == "__main__":
    unittest.main()
