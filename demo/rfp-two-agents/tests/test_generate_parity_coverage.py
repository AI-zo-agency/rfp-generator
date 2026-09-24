"""Second-pass audit: demo generate covers production post-outline steps."""

from __future__ import annotations

import inspect
import unittest

import server
from progress_bus import GENERATE_STEPS


# User-listed production gaps that must appear in the demo generate path.
REQUIRED_STEP_IDS = (
    "seed_rfp",
    "writing_briefs",
    "phase2_finalize",
    "phase3",
    "phase3_5",
    "closing_submission",
    "structure_coverage",
    "phase3_6",
    "phase4",
    "build_finalize",
    "ready_export",
)

REQUIRED_CALL_SNIPPETS = (
    "finalize_phase2_research_from_plan",
    "run_phase3_drafting",
    "run_phase3_5_budget",
    "run_post_budget_attach_passes",
    "run_phase3_6_self_edit",
    "run_phase4_presubmit_review",
    "run_build_finalize_pass",
    "phase35_budget_gate",
)

FORBIDDEN_SOFT_GATE_SNIPPETS = (
    "force readiness",
    "Demo force readiness",
)


class GenerateParityCoverageTests(unittest.TestCase):
    def test_generate_steps_catalog_covers_required_ids(self) -> None:
        ids = [s["step"] for s in GENERATE_STEPS]
        for required in REQUIRED_STEP_IDS:
            self.assertIn(required, ids, f"GENERATE_STEPS missing {required!r}")
        # Ordering: Phase 2 before Phase 3; closing before 3.6; P4 before finalize.
        self.assertLess(ids.index("phase2_finalize"), ids.index("phase3"))
        self.assertLess(ids.index("closing_submission"), ids.index("phase3_6"))
        self.assertLess(ids.index("structure_coverage"), ids.index("phase3_6"))
        self.assertLess(ids.index("phase4"), ids.index("build_finalize"))

    def test_generate_source_wires_production_helpers(self) -> None:
        seed_src = inspect.getsource(server._seed_demo_rfp_for_generate)
        run_src = inspect.getsource(server._run_generate_proposal)
        combined = seed_src + "\n" + run_src
        for snippet in REQUIRED_CALL_SNIPPETS:
            self.assertIn(snippet, combined, f"missing call/wire {snippet!r}")
        for bad in FORBIDDEN_SOFT_GATE_SNIPPETS:
            self.assertNotIn(bad, combined, f"soft-gate remnant: {bad!r}")

    def test_seed_hard_blocks_non_ready_validation(self) -> None:
        seed_src = inspect.getsource(server._seed_demo_rfp_for_generate)
        self.assertIn("status_code=422", seed_src)
        self.assertIn("readiness_status == \"blocked\"", seed_src)


if __name__ == "__main__":
    unittest.main()
