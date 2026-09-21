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


class ExportTitleTests(unittest.TestCase):
    def test_uses_pdf_stem_when_form_title_is_placeholder(self) -> None:
        title = server._export_title_from_session(
            {
                "rfp_meta": {"title": "Demo RFP"},
                "pdf_filename": "Wildfire Preparedness Marketing for Oregon State Fire Marshal.pdf",
            }
        )
        self.assertEqual(
            title,
            "Wildfire Preparedness Marketing for Oregon State Fire Marshal",
        )

    def test_keeps_explicit_form_title(self) -> None:
        title = server._export_title_from_session(
            {
                "rfp_meta": {"title": "OSFM Wildfire Campaign"},
                "pdf_filename": "something-else.pdf",
            }
        )
        self.assertEqual(title, "OSFM Wildfire Campaign")


class GenerateResponseShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_generate_soft_skips_budget_ambiguity_and_continues(self) -> None:
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
        research = MagicMock()

        with (
            patch.object(server, "_seed_demo_rfp_for_generate", new=AsyncMock(return_value=plan)),
            patch(
                "app.services.proposal_generator.run_phase3_drafting",
                new=AsyncMock(return_value=(draft, research)),
            ),
            patch(
                "app.services.proposal_generator.run_phase3_5_budget",
                new=AsyncMock(side_effect=AssertionError("budget must not run on block")),
            ),
            patch(
                "app.services.proposal_generator.run_post_budget_attach_passes",
                new=AsyncMock(return_value=(draft, research)),
            ),
            patch(
                "app.services.proposal_generator.run_phase3_6_self_edit",
                new=AsyncMock(return_value=(draft, research, {})),
            ),
            patch(
                "app.services.proposal_generator.run_phase4_presubmit_review",
                new=AsyncMock(return_value=(MagicMock(), research)),
            ),
            patch(
                "app.services.proposal_fulfill_rfp_gaps.run_build_finalize_pass",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.proposal_repository.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_repository.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
            patch.object(server, "_cost_for", return_value={"total_cost_usd": 0.01}),
        ):
            out = await server._run_generate_proposal(demo_id=demo_id, bus=None)

        self.assertTrue(out["draft_ready"])
        self.assertEqual(out["budgetGate"]["status"], "skipped")
        self.assertIn("ambiguous", (out["budgetGate"].get("detail") or "").casefold())
        del server._SESSIONS[demo_id]

    async def test_run_generate_skips_budget_when_absent_and_runs_tail(self) -> None:
        demo_id = "rfpda-gen-absent"
        plan = ProposalExecutionPlan(
            rfpId=demo_id,
            writing={
                "proposalOutline": ProposalOutline(
                    sections=[
                        OutlineSection(id="s1", title="Letter", order=1),
                    ]
                ),
                "costRequirementStatus": "absent",
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
        research = MagicMock()

        with (
            patch.object(server, "_seed_demo_rfp_for_generate", new=AsyncMock(return_value=plan)),
            patch(
                "app.services.proposal_generator.run_phase3_drafting",
                new=AsyncMock(return_value=(draft, research)),
            ),
            patch(
                "app.services.proposal_generator.run_phase3_5_budget",
                new=AsyncMock(side_effect=AssertionError("budget must not run when absent")),
            ),
            patch(
                "app.services.proposal_generator.run_post_budget_attach_passes",
                new=AsyncMock(return_value=(draft, research)),
            ),
            patch(
                "app.services.proposal_generator.run_phase3_6_self_edit",
                new=AsyncMock(return_value=(draft, research, {})),
            ),
            patch(
                "app.services.proposal_generator.run_phase4_presubmit_review",
                new=AsyncMock(return_value=(MagicMock(), research)),
            ),
            patch(
                "app.services.proposal_fulfill_rfp_gaps.run_build_finalize_pass",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.proposal_repository.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_repository.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
            patch.object(server, "_cost_for", return_value={"total_cost_usd": 0.01}),
        ):
            out = await server._run_generate_proposal(demo_id=demo_id, bus=None)

        self.assertTrue(out["draft_ready"])
        self.assertEqual(out["sectionCount"], 2)
        self.assertEqual(out["budgetGate"]["status"], "skipped")
        self.assertTrue(sess["draft_ready"])
        del server._SESSIONS[demo_id]

    async def test_resume_skips_phase3_when_draft_exists(self) -> None:
        demo_id = "rfpda-gen-resume"
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

        sec = MagicMock()
        sec.content = "Drafted body already present."
        draft = MagicMock()
        draft.sections = [sec]
        research = MagicMock()
        research.presubmit_review = None  # mid-pipeline: draft yes, Phase 4 no
        seed = AsyncMock(side_effect=AssertionError("seed must not run on resume"))
        phase3 = AsyncMock(side_effect=AssertionError("phase3 must not run on resume"))
        attach = AsyncMock(return_value=(draft, research))

        with (
            patch.object(server, "_seed_demo_rfp_for_generate", new=seed),
            patch(
                "app.services.proposal_generator.run_phase3_drafting",
                new=phase3,
            ),
            patch(
                "app.services.proposal_generator.run_phase3_5_budget",
                new=AsyncMock(side_effect=AssertionError("budget must not run")),
            ),
            patch(
                "app.services.proposal_generator.run_post_budget_attach_passes",
                new=attach,
            ),
            patch(
                "app.services.proposal_generator.run_phase3_6_self_edit",
                new=AsyncMock(return_value=(draft, research, {})),
            ),
            patch(
                "app.services.proposal_generator.run_phase4_presubmit_review",
                new=AsyncMock(return_value=(MagicMock(), research)),
            ),
            patch(
                "app.services.proposal_fulfill_rfp_gaps.run_build_finalize_pass",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.proposal_repository.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_repository.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
            patch.object(server, "_cost_for", return_value={"total_cost_usd": 0.01}),
            patch.object(server, "set_checkpoint_draft_ready"),
        ):
            out = await server._run_generate_proposal(
                demo_id=demo_id, bus=None, resume=True
            )

        self.assertTrue(out["draft_ready"])
        self.assertTrue(out["resumed"])
        self.assertFalse(out.get("alreadyComplete"))
        self.assertEqual(out["budgetGate"]["status"], "skipped")
        seed.assert_not_called()
        phase3.assert_not_called()
        attach.assert_awaited()
        del server._SESSIONS[demo_id]

    async def test_resume_short_circuits_when_already_complete(self) -> None:
        demo_id = "rfpda-gen-done"
        plan = ProposalExecutionPlan(
            rfpId=demo_id,
            writing={
                "proposalOutline": ProposalOutline(
                    sections=[OutlineSection(id="s1", title="Letter", order=1)]
                ),
                "costRequirementStatus": "ambiguous",
            },
        )
        sess = {
            "plan": plan.model_dump(by_alias=True),
            "rfp_text": "x" * 250,
            "rfp_meta": {"title": "Smoke"},
            "run_id": "run-1",
            "draft_ready": False,
        }
        server._SESSIONS[demo_id] = sess
        sec = MagicMock()
        sec.content = "Done."
        draft = MagicMock()
        draft.sections = [sec]
        research = MagicMock()
        research.presubmit_review = {"ready": False}  # Phase 4 already ran
        attach = AsyncMock(side_effect=AssertionError("must not re-run closing"))

        with (
            patch(
                "app.services.proposal_repository.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_repository.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
            patch(
                "app.services.proposal_generator.run_post_budget_attach_passes",
                new=attach,
            ),
            patch.object(server, "_cost_for", return_value={"total_cost_usd": 0.01}),
            patch.object(server, "set_checkpoint_draft_ready") as mark,
        ):
            out = await server._run_generate_proposal(
                demo_id=demo_id, bus=None, resume=True
            )

        self.assertTrue(out["alreadyComplete"])
        self.assertTrue(out["draft_ready"])
        attach.assert_not_called()
        mark.assert_called()
        del server._SESSIONS[demo_id]


if __name__ == "__main__":
    unittest.main()
