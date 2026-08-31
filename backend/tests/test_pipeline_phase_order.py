"""Pipeline phase order — build-finalize must chain after pre-submit review."""

from __future__ import annotations

import unittest

from app.services.proposal_pipeline_checkpoint import (
    PIPELINE_PHASES,
    _next_phase_after,
)


class PipelinePhaseOrderTests(unittest.TestCase):
    def test_build_finalize_follows_phase_4(self) -> None:
        self.assertEqual(_next_phase_after("phase-4-review"), "build-finalize")

    def test_build_finalize_is_last_generate_phase(self) -> None:
        idx = PIPELINE_PHASES.index("build-finalize")
        self.assertEqual(PIPELINE_PHASES[idx + 1:], ())
        self.assertEqual(_next_phase_after("build-finalize"), "complete")

    def test_all_generate_phases_present(self) -> None:
        expected = (
            "sections-1-3",
            "phase-2",
            "phase-3",
            "phase-3-5-budget",
            "phase-3-6-self-edit",
            "phase-4-review",
            "build-finalize",
        )
        self.assertEqual(PIPELINE_PHASES, expected)


if __name__ == "__main__":
    unittest.main()
