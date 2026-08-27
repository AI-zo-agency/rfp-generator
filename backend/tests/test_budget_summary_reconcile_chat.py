"""Budget-tab Improve syncs fee labels from the canonical ledger.

improve_proposal_section() previously referenced the local variable `section`
when calling _try_budget_summary_reconcile() before `section` had been
assigned, raising UnboundLocalError. These tests cover that path plus
ledger-driven Professional fees sync (no user-message keyword gate).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_section_editor import improve_proposal_section


def _section(*, content: str = "Total proposed investment: $325,242.66 ...") -> ProposalSection:
    return ProposalSection(
        id="section-budget",
        title="Proposed budget and cost structure",
        content=content,
        source="rfp",
        mode="write",
        wordTarget=400,
        status="generated",
    )


def _draft(section: ProposalSection | None = None) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-budget-reconcile",
        sections=[section or _section()],
        updatedAt="2026-07-27T00:00:00+00:00",
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp-budget-reconcile",
        title="Test RFP",
        client="Test Client",
        sector="Municipal",
        source="manual",
        dueDate="2026-08-01",
        receivedDate="2026-07-01",
        lastActivity="2026-07-01",
        lastActivityNote="test",
    )


def _budget_with_fees_and_travel() -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-budget-reconcile",
        updatedAt="2026-08-27T00:00:00+00:00",
        lineItems=[
            BudgetLineItem(
                id="1",
                description="Strategy phase",
                category="Fees",
                quantity=1,
                unit="project",
                rate=210000,
                extended=210000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="2",
                description="Travel",
                category="Travel",
                quantity=1,
                unit="project",
                rate=3500,
                extended=3500,
                lineItemType="direct_expense",
            ),
        ],
        agencyFeeSubtotal=210000,
        directExpensesTotal=3500,
        agencyRevenueEstimate=213500,
        totalClientInvoicing=213500,
    )


class BudgetSummaryReconcileChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_reconcile_ask_does_not_raise_unbound_local(self) -> None:
        draft = _draft()

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.proposal_section_editor.aload_rfp_for_proposal",
                new=AsyncMock(return_value=(_rfp(), "", "RFP text")),
            ),
            patch(
                "app.services.proposal_section_editor.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_section_editor.aget_research_cache",
                new=AsyncMock(return_value=None),
            ),
        ):
            (
                section,
                _updated_draft,
                _research,
                _provider,
                reply,
                changed,
                _suggested,
            ) = await improve_proposal_section(
                "rfp-budget-reconcile",
                "section-budget",
                (
                    "Please reconcile the investment summary — the "
                    "totals don't match the fee table."
                ),
                persist=False,
            )

        self.assertEqual(section.id, "section-budget")
        self.assertFalse(changed)
        self.assertIn("no canonical fee table", reply)

    async def test_budget_tab_syncs_professional_fees_from_ledger_without_keywords(
        self,
    ) -> None:
        """Improve on Price with a wrong fees label — ledger wins, no keyword ask."""
        section = _section(
            content=(
                "**Professional fees: $213,500**\n\n"
                "| Phase | Amount |\n| --- | --- |\n"
                "| Strategy | $210,000 |\n"
                "| Travel | $3,500 |\n"
            )
        )
        draft = _draft(section)
        research = ProposalResearchCache(
            rfpId="rfp-budget-reconcile",
            updatedAt="2026-08-27T00:00:00+00:00",
            budget=_budget_with_fees_and_travel(),
        )

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.proposal_section_editor.aload_rfp_for_proposal",
                new=AsyncMock(return_value=(_rfp(), "", "RFP text")),
            ),
            patch(
                "app.services.proposal_section_editor.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_section_editor.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
        ):
            (
                out_section,
                _updated_draft,
                _research,
                _provider,
                reply,
                changed,
                _suggested,
            ) = await improve_proposal_section(
                "rfp-budget-reconcile",
                "section-budget",
                "Improve this section",
                persist=False,
                improve_section_pinned=True,
            )

        self.assertTrue(changed)
        self.assertIn("Professional fees: $210,000", out_section.content or "")
        self.assertNotIn("Professional fees: $213,500", out_section.content or "")
        self.assertIn("Synced investment summary labels", reply)


if __name__ == "__main__":
    unittest.main()
