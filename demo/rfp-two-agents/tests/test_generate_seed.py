"""Unit smoke for generate seed helpers (protectFromCap + response shape)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import server
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
)


class ProtectFromCapStampTests(unittest.TestCase):
    def test_stamps_every_outline_section(self) -> None:
        plan = ProposalExecutionPlan(
            rfpId="rfpda-test",
            writing={
                "proposalOutline": {
                    "sections": [
                        {"id": "a", "title": "Cover", "order": 1},
                        {"id": "b", "title": "Approach", "order": 2},
                    ]
                }
            },
        )
        self.assertFalse(plan.writing.proposal_outline.sections[0].protect_from_cap)
        n = server._stamp_outline_protect_from_cap(plan)
        self.assertEqual(n, 2)
        self.assertTrue(all(s.protect_from_cap for s in plan.writing.proposal_outline.sections))


class GenerateResponseShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_generate_soft_skips_budget_block(self) -> None:
        demo_id = "rfpda-gen-smoke"
        plan = ProposalExecutionPlan(
            rfpId=demo_id,
            writing={
                "proposalOutline": ProposalOutline(
                    sections=[
                        OutlineSection(id="s1", title="Letter", order=1),
                    ]
                ),
                "costRequirementStatus": "ambiguous",
            },
        )
        plan.validation.readiness_status = "ready"
        sess = {
            "plan": plan.model_dump(by_alias=True),
            "rfp_text": "x" * 250,
            "rfp_meta": {"title": "Smoke", "client": "Test"},
            "run_id": "run-1",
            "draft_ready": False,
        }
        server._SESSIONS[demo_id] = sess

        draft = MagicMock()
        draft.sections = [MagicMock(), MagicMock()]

        with (
            patch.object(server, "_seed_demo_rfp_for_generate", new=AsyncMock(return_value=plan)),
            patch(
                "app.services.proposal_generator.run_phase3_drafting",
                new=AsyncMock(return_value=(draft, MagicMock())),
            ),
            patch(
                "app.services.proposal_generator.run_phase3_5_budget",
                new=AsyncMock(side_effect=AssertionError("budget must not run on block")),
            ),
            patch(
                "app.services.proposal_generator.run_phase3_6_self_edit",
                new=AsyncMock(return_value=(draft, MagicMock(), {})),
            ),
            patch.object(server, "_cost_for", return_value={"total_cost_usd": 0.01}),
        ):
            out = await server._run_generate_proposal(demo_id=demo_id, bus=None)

        self.assertTrue(out["draft_ready"])
        self.assertEqual(out["sectionCount"], 2)
        self.assertEqual(out["budgetGate"]["status"], "skipped")
        self.assertIn("costRequirementStatus", out)
        self.assertTrue(sess["draft_ready"])
        del server._SESSIONS[demo_id]


if __name__ == "__main__":
    unittest.main()
