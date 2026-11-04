"""Typing "generate budget" in chat must run the budget step, as Ralph's refusal says it will."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.proposal import (
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_budget_playbook import user_orders_budget_generation
from app.services.proposal_section_editor import improve_proposal_section

NOW = "2026-11-03T00:00:00Z"
STUB = "[MANUAL FILL: Draft full «6. Cost and Price Proposal» for this RFP.]"
FEE_TABLE = "| Task | Investment |\n|---|---:|\n| A1 | $3,000 |\n"


class BudgetOrderWordingTests(unittest.TestCase):
    def test_orders_to_generate_are_recognised(self) -> None:
        for text in (
            "generate budget",
            "geenrate budget",
            "Generate the budget",
            "rebuild budget from the pricing guide",
            "Regenerate the budget table for this tab.",
            "please create the fee table",
        ):
            with self.subTest(text):
                self.assertTrue(user_orders_budget_generation(text))

    def test_other_requests_are_not_orders(self) -> None:
        for text in (
            "Improve this section for the RFP.",
            "fix the cost proposal wording",
            "make the budget section shorter",
            "why did generate budget fail?",
            "how do I generate a budget",
            "generate a budget summary paragraph",
            "generate a timeline for the rollout",
            "please generate a new narrative that explains how our team keeps the "
            "project budget on track over the full eighteen month term",
        ):
            with self.subTest(text):
                self.assertFalse(user_orders_budget_generation(text))


def _draft(content: str) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(id="rfp-sec-1", title="Approach", content="Our approach.", status="generated"),
            ProposalSection(id="rfp-sec-6", title="Cost and Price Proposal", content=content, status="generated"),
        ],
        updatedAt=NOW,
    )


class ChatRunsBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def _improve(self, message: str, budget_step: AsyncMock, **kwargs):
        with (
            patch("app.services.proposal_section_editor.llm.is_configured", return_value=True),
            patch(
                "app.services.proposal_section_editor.aload_rfp_for_proposal",
                new=AsyncMock(return_value=(MagicMock(title="T", client="C"), MagicMock(description="", pdf_text=""), "rfp")),
            ),
            patch(
                "app.services.proposal_section_editor.aget_proposal_draft",
                new=AsyncMock(return_value=_draft(STUB)),
            ),
            patch(
                "app.services.proposal_section_editor.aget_research_cache",
                new=AsyncMock(return_value=ProposalResearchCache(rfpId="rfp-1", updatedAt=NOW)),
            ),
            patch(
                "app.services.proposal_section_editor._maybe_rename_sidebar_title",
                new=AsyncMock(return_value=(_draft(STUB), None)),
            ),
            # Anything past the budget branch is the normal editor, which calls live models.
            patch(
                "app.services.proposal_section_editor._try_manual_fill_resolution",
                new=AsyncMock(side_effect=RuntimeError("reached normal path")),
            ),
            patch("app.services.proposal_generator.run_phase3_5_budget", new=budget_step),
        ):
            return await improve_proposal_section("rfp-1", "rfp-sec-6", message, **kwargs)

    def _succeeds(self) -> AsyncMock:
        research = ProposalResearchCache(rfpId="rfp-1", updatedAt=NOW)
        budget = ProposalBudget(rfpId="rfp-1", updatedAt=NOW)
        return AsyncMock(return_value=(_draft(FEE_TABLE), research, budget))

    async def test_generate_budget_runs_the_budget_step_and_returns_the_new_tab(self) -> None:
        step = self._succeeds()
        section, draft, _r, _p, reply, changed, _f = await self._improve("generate budget", step)
        step.assert_awaited_once_with("rfp-1", force=True)
        self.assertTrue(changed)
        self.assertEqual(section.id, "rfp-sec-6")
        self.assertIn("$3,000", section.content)
        self.assertIn("Cost and Price Proposal", reply)

    async def test_a_failed_budget_step_says_why_and_changes_nothing(self) -> None:
        step = AsyncMock(side_effect=RuntimeError("Pricing plan LLM returned no plan"))
        section, _d, _r, _p, reply, changed, _f = await self._improve("generate budget", step)
        self.assertFalse(changed)
        self.assertEqual(section.content, STUB)
        self.assertIn("Pricing plan LLM returned no plan", reply)

    async def test_budget_step_that_writes_nothing_is_not_reported_as_success(self) -> None:
        research = ProposalResearchCache(rfpId="rfp-1", updatedAt=NOW)
        step = AsyncMock(
            return_value=(_draft(STUB), research, ProposalBudget(rfpId="rfp-1", updatedAt=NOW))
        )
        _s, _d, _r, _p, reply, changed, _f = await self._improve("generate budget", step)
        self.assertFalse(changed)
        self.assertIn("no fee table", reply.casefold())

    async def test_unsaved_background_fill_never_starts_the_budget_step(self) -> None:
        step = self._succeeds()
        with self.assertRaisesRegex(RuntimeError, "reached normal path"):
            await self._improve("generate budget", step, persist=False)
        step.assert_not_awaited()


class BudgetOrderSkipsPreviewTests(unittest.TestCase):
    """The app sends chat as a preview by default, and the budget step saves as it runs."""

    def test_budget_order_is_not_a_preview(self) -> None:
        from app.api.v1.proposals import _chat_runs_as_preview

        self.assertFalse(_chat_runs_as_preview(True, "generate budget"))

    def test_ordinary_chat_stays_a_preview(self) -> None:
        from app.api.v1.proposals import _chat_runs_as_preview

        self.assertTrue(_chat_runs_as_preview(True, "Improve this section for the RFP."))

    def test_chat_not_sent_as_a_preview_stays_that_way(self) -> None:
        from app.api.v1.proposals import _chat_runs_as_preview

        self.assertFalse(_chat_runs_as_preview(False, "Improve this section for the RFP."))
