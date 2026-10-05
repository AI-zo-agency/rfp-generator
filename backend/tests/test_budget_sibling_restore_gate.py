"""Full ledger overwrite must hit only the canonical fee tab — not Cost Analysis."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_budget_kpi import restore_unresolved_budget_token_tabs

_LEDGER = (
    "## Proposed Investment\n\n"
    "| Item | Amount |\n| --- | ---: |\n"
    "| Agency fees | $12,000 |\n\n"
    "**Total proposed investment: $12,000**"
)


def _budget() -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-wy",
        updatedAt="2026-10-05T00:00:00+00:00",
        budgetFormat="pricing_plan",
        pricingPlan={"pricing_version": "v2", "tasks": []},
        lumpSumTotal=12_000,
        totalClientInvoicing=12_000,
    )


class SiblingCostAnalysisRestoreGate(unittest.TestCase):
    @patch(
        "app.services.proposal_fulfill_rfp_budget_kpi.render_budget_markdown",
        return_value=_LEDGER,
    )
    def test_cost_analysis_keeps_rationale_when_price_sheet_is_canonical(
        self, _render
    ) -> None:
        rationale = (
            "We price transparently with no hidden media markup. "
            "Detailed agency fees appear in the Proposal Price Sheet. "
            "Unresolved cell: {{budget.agency_fee_subtotal}}."
        )
        draft = ProposalDraft(
            rfpId="rfp-wy",
            updatedAt="2026-10-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="s10",
                    title="Cost Analysis",
                    content=rationale,
                    status="generated",
                ),
                ProposalSection(
                    id="s11",
                    title=(
                        "Section 9 — Proposal Price Sheet and Signature Page "
                        "(Pricing Schedule 1)"
                    ),
                    content="{{budget.total_client_invoicing}}",
                    status="generated",
                ),
            ],
        )
        out, logs = restore_unresolved_budget_token_tabs(draft, _budget())
        cost = next(s for s in out.sections if s.title == "Cost Analysis")
        price = next(s for s in out.sections if "Price Sheet" in (s.title or ""))
        self.assertNotIn("## Proposed Investment", cost.content or "")
        self.assertNotIn("$12,000", cost.content or "")
        self.assertIn("Proposal Price Sheet", cost.content or "")
        self.assertIn("$12,000", price.content or "")
        self.assertTrue(any("sibling" in line.lower() for line in logs))

    @patch(
        "app.services.proposal_fulfill_rfp_budget_kpi.render_budget_markdown",
        return_value=_LEDGER,
    )
    def test_canonical_alone_still_gets_full_ledger(self, _render) -> None:
        draft = ProposalDraft(
            rfpId="rfp-wy",
            updatedAt="2026-10-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="s11",
                    title="Proposal Price Sheet (Pricing Schedule 1)",
                    content="Fees: {{budget.unknown_slot}}",
                    status="generated",
                ),
            ],
        )
        out, _logs = restore_unresolved_budget_token_tabs(draft, _budget())
        body = out.sections[0].content or ""
        self.assertEqual(body, _LEDGER)
        self.assertNotIn("{{budget.", body)


if __name__ == "__main__":
    unittest.main()
