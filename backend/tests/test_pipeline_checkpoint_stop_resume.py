"""record_generation_stopped must never fabricate a resume phase.

Regression for: user stops generation mid "Final checks" (build-finalize,
the last step of an 18-step pass with long Sonnet-5 LLM calls), but at the
exact moment /proposal/stop reads the checkpoint, `in_progress_phase` has
already gone falsy (e.g. staleness cleanup cleared it first). The old code
defaulted the unknown phase to the literal string "phase-3" — which is
itself a valid PIPELINE_PHASES member, so it silently won the
`active in PIPELINE_PHASES` check ahead of the already-correct
`prior.resume_from_phase` fallback a few lines below it. The next
"Continue proposal" then rewound a near-finished draft all the way back to
drafting, re-running (and re-billing) budget, self-edit, review, and
build-finalize on top of an already-good draft.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import ProposalPipelineCheckpoint, ProposalResearchCache
from app.services import proposal_pipeline_checkpoint as checkpoint_mod


class RecordGenerationStoppedResumeTests(unittest.IsolatedAsyncioTestCase):
    def _mock_research(self, prior: ProposalPipelineCheckpoint | None) -> ProposalResearchCache:
        return ProposalResearchCache(
            rfpId="rfp-1",
            updatedAt="2026-09-01T00:00:00+00:00",
            pipelineCheckpoint=prior,
        )

    async def _run_stop(
        self, prior: ProposalPipelineCheckpoint | None, phase: str | None
    ) -> ProposalPipelineCheckpoint:
        research = self._mock_research(prior)
        saved: dict[str, ProposalResearchCache] = {}

        async def fake_get(rfp_id: str):
            return research

        async def fake_save(updated: ProposalResearchCache):
            saved["value"] = updated

        with mock.patch.object(checkpoint_mod, "aget_research_cache", fake_get), \
                mock.patch.object(checkpoint_mod, "asave_research_cache", fake_save):
            await checkpoint_mod.record_generation_stopped("rfp-1", phase)

        return saved["value"].pipeline_checkpoint

    async def test_stop_mid_build_finalize_with_cleared_in_progress_resumes_build_finalize(
        self,
    ) -> None:
        """The exact bug repro: in_progress_phase already None, but the run was
        genuinely mid build-finalize (proven by resume_fulfill_step)."""
        prior = ProposalPipelineCheckpoint(
            lastCompletedPhase="phase-4-review",
            inProgressPhase=None,
            resumeFromPhase="build-finalize",
            stepIndex=5,
            stepTotal=19,
            resumeFulfillStep=5,
            updatedAt="2026-09-01T00:00:00+00:00",
        )
        result = await self._run_stop(prior, None)
        self.assertEqual(result.resume_from_phase, "build-finalize")
        self.assertNotEqual(result.resume_from_phase, "phase-3")

    async def test_stop_with_in_progress_phase_still_set_resumes_that_phase(self) -> None:
        prior = ProposalPipelineCheckpoint(
            lastCompletedPhase="phase-4-review",
            inProgressPhase="build-finalize",
            resumeFromPhase="build-finalize",
            updatedAt="2026-09-01T00:00:00+00:00",
        )
        result = await self._run_stop(prior, "build-finalize")
        self.assertEqual(result.resume_from_phase, "build-finalize")

    async def test_stop_with_no_prior_checkpoint_does_not_fabricate_phase_3(self) -> None:
        result = await self._run_stop(None, None)
        self.assertIsNone(result.resume_from_phase)
        self.assertNotEqual(result.resume_from_phase, "phase-3")

    async def test_stop_mid_phase_3_still_resumes_phase_3(self) -> None:
        """Genuine phase-3 stops must keep working — this is not disabling resume."""
        prior = ProposalPipelineCheckpoint(
            lastCompletedPhase="phase-2",
            inProgressPhase="phase-3",
            resumeFromPhase="phase-3",
            updatedAt="2026-09-01T00:00:00+00:00",
        )
        result = await self._run_stop(prior, "phase-3")
        self.assertEqual(result.resume_from_phase, "phase-3")


if __name__ == "__main__":
    unittest.main()
