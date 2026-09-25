"""Compliance gaps for pricing-plan (v2) budgets vs frozen legacy budgets."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_rfp_compliance import (
    scan_budget_revenue_gaps,
    scan_submission_pricing_flag_gaps,
)

_TS = "2026-09-25T00:00:00+00:00"
_NOTES = [
    "[PRICING NOTE — Sonja: confirm media buy cadence]",
    "[PRICING NOTE — tier: Average — mid-size agency scope]",
]


def _case(*, plan: dict | None, flags: list[str]):
    budget = ProposalBudget(
        pricingPlan=plan,
        rfpId="r",
        updatedAt=_TS,
        pricingFlags=flags,
        lineItems=[
            BudgetLineItem(
                id="L1", description="Strategy", category="Labor",
                extended=40_000, lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="L2", description="Paid media placement", category="6.1 Media",
                extended=60_000, lineItemType="client_passthrough",
            ),
        ],
        lumpSumTotal=100_000,
    )
    draft = ProposalDraft(
        rfpId="r",
        sections=[
            ProposalSection(
                id="section-budget-pricing", title="Budget & Pricing",
                content="| Item | Amount |\n| --- | ---: |\n| Strategy | $40,000 |\n",
                status="generated",
            )
        ],
        updatedAt=_TS,
    )
    return draft, ProposalResearchCache(rfpId="r", updatedAt=_TS, budget=budget)


class PricingPlanComplianceGapTests(unittest.TestCase):
    def test_v2_notes_and_media_line_produce_no_gaps(self) -> None:
        draft, research = _case(plan={"tier": "Average"}, flags=list(_NOTES))
        self.assertEqual(scan_submission_pricing_flag_gaps(draft=draft, research=research), [])
        self.assertEqual(scan_budget_revenue_gaps(draft=draft, research=research), [])

    def test_v2_unresolved_pricing_flag_still_gaps(self) -> None:
        draft, research = _case(
            plan={"tier": "Average"},
            flags=[*_NOTES, "[PRICING FLAG: total does not match task table]"],
        )
        gaps = scan_submission_pricing_flag_gaps(draft=draft, research=research)
        self.assertEqual(len(gaps), 1)

    def test_legacy_behavior_unchanged(self) -> None:
        draft, research = _case(plan=None, flags=["any legacy flag"])
        self.assertEqual(
            len(scan_submission_pricing_flag_gaps(draft=draft, research=research)), 1
        )
        self.assertEqual(len(scan_budget_revenue_gaps(draft=draft, research=research)), 1)


if __name__ == "__main__":
    unittest.main()
