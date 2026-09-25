"""Legacy (pre-v2) budgets are frozen: budget code never rewrites their Cost section."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection

LEGACY = ProposalBudget(
    rfpId="r-old", updatedAt="t", budgetFormat="phased",
    lineItems=[BudgetLineItem(id="li-1", category="fee", description="Strategy", unit="project",
                              rate=5000, quantity=1, extended=5000)],
)
# No "## Budget & Pricing" heading: the (non-budget) integrity guard strips a
# leading title-echo heading from every section. The prose total disagrees with
# the ledger on purpose — the legacy summary reconcile would rewrite it.
SAVED_COST = "**Professional fees: $4,500**\n\n| Phase | Fee |\n|---|---:|\n| Strategy | $5,000 |\n"


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="r-old", updatedAt="t",
        sections=[
            ProposalSection(id="s1", title="Approach", content="We plan.", status="generated"),
            ProposalSection(id="s2", title="Budget & Pricing", content=SAVED_COST, status="generated"),
        ],
    )


class FrozenLegacyBudgetTests(unittest.TestCase):
    def test_render_returns_empty_for_legacy_budget(self) -> None:
        from app.services.proposal_budget_content import render_budget_markdown

        self.assertEqual(render_budget_markdown(LEGACY, rfp_text="rfp"), "")

    def test_persist_guard_leaves_legacy_cost_section_untouched(self) -> None:
        from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards

        out, _ = apply_zero_fabrication_guards(_draft(), budget=LEGACY, rfp_text="rfp")
        self.assertEqual(out.sections[1].content, SAVED_COST)


class FrozenReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_on_legacy_budget_saves_nothing(self) -> None:
        from app.models.proposal import ProposalResearchCache
        from app.services import proposal_generator as gen

        research = ProposalResearchCache(rfpId="r-old", updatedAt="t", budget=LEGACY)
        save_draft = AsyncMock()
        save_research = AsyncMock()
        with patch("app.services.proposal_pricing_service.aget_research_cache",
                   AsyncMock(return_value=research)), \
             patch.object(gen, "aget_proposal_draft", AsyncMock(return_value=_draft())), \
             patch.object(gen, "asave_proposal_draft", save_draft), \
             patch.object(gen, "asave_research_cache", save_research):
            draft, _research, budget = await gen.run_phase3_5_budget_reconcile("r-old")
        save_draft.assert_not_awaited()
        save_research.assert_not_awaited()
        self.assertEqual(draft.sections[1].content, SAVED_COST)
        self.assertIs(budget, LEGACY)
