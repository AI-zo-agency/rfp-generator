"""Tests for deterministic budget reconciliation and validation."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import (
    adjust_pm_line_items_to_guide,
    assert_budget_canonical,
    collect_pm_ratio_violations,
    reconcile_proposal_budget,
)


def _line(
    *,
    item_id: str,
    description: str,
    extended: float,
    category: str = "Digital Marketing",
) -> BudgetLineItem:
    return BudgetLineItem(
        id=item_id,
        category=category,
        description=description,
        extended=extended,
        lineItemType="agency_fee",
    )


class ProposalBudgetValidationTests(unittest.TestCase):
    def test_adjust_pm_line_items_scales_down_high_pm_ratio(self) -> None:
        items = [
            _line(item_id="strategy", description="Digital campaign strategy", extended=187_500),
            _line(
                item_id="pm",
                description="Project management — annual",
                extended=30_000,
                category="Account & Project Management",
            ),
        ]
        adjusted, note = adjust_pm_line_items_to_guide(items, agency_base=217_500)
        self.assertIsNotNone(note)
        pm_total = sum(float(i.extended or 0) for i in adjusted if "project management" in i.description.lower())
        ratio = pm_total / 217_500
        self.assertGreaterEqual(ratio, 0.05)
        self.assertLessEqual(ratio, 0.08)
        self.assertLess(pm_total, 30_000)

    def test_adjust_pm_scales_up_from_just_under_five_percent(self) -> None:
        """Regression: inclusive-base scaling left 4.9% stuck under 5% and halted the pipeline."""
        items = [
            _line(item_id="strategy", description="Integrated digital strategy", extended=152_587),
            _line(
                item_id="pm",
                description="Project management — annual",
                extended=7_862,
                category="Account & Project Management",
            ),
        ]
        agency_total = 160_449  # 7862 / 0.049
        adjusted, note = adjust_pm_line_items_to_guide(items, agency_base=agency_total)
        self.assertIsNotNone(note)
        pm_total = sum(
            float(i.extended or 0)
            for i in adjusted
            if "project management" in i.description.lower()
        )
        non_pm = sum(
            float(i.extended or 0)
            for i in adjusted
            if "project management" not in i.description.lower()
        )
        ratio = pm_total / (non_pm + pm_total)
        self.assertGreaterEqual(ratio, 0.05)
        self.assertLessEqual(ratio, 0.08)

        budget = ProposalBudget(
            rfpId="test-rfp-pm49",
            updatedAt="2026-07-20T00:00:00Z",
            lineItems=items,
            agencyFeeSubtotal=agency_total,
            agencyRevenueEstimate=agency_total,
            lineItemSum=agency_total,
        )
        reconciled = reconcile_proposal_budget(budget)
        assert_budget_canonical(reconciled)
        self.assertEqual(collect_pm_ratio_violations(reconciled), [])

    def test_reconcile_strips_misplaced_verify_flags(self) -> None:
        budget = ProposalBudget(
            rfpId="test-rfp",
            updatedAt="2026-07-17T00:00:00Z",
            lineItems=[
                _line(item_id="pm", description="Project management", extended=10_000, category="Account & Project Management"),
                _line(item_id="strategy", description="Campaign strategy", extended=190_000),
            ],
            agencyFeeSubtotal=200_000,
            agencyRevenueEstimate=200_000,
            lineItemSum=200_000,
            pricingFlags=[
                "[VERIFY: Mississippi state registration — out-of-state vendor authorization required]",
                "[PRICING FLAG: Sonja to confirm burdened rates]",
            ],
        )
        reconciled = reconcile_proposal_budget(budget)
        joined = "\n".join(reconciled.pricing_flags)
        self.assertNotIn("[VERIFY:", joined)
        self.assertIn("PRICING FLAG", joined)

    def test_reconcile_auto_scales_pm_and_passes_validation(self) -> None:
        budget = ProposalBudget(
            rfpId="test-rfp",
            updatedAt="2026-07-17T00:00:00Z",
            lineItems=[
                _line(item_id="strategy", description="Integrated digital media strategy", extended=187_500),
                _line(
                    item_id="pm",
                    description="Project management — annual account",
                    extended=30_000,
                    category="Account & Project Management",
                ),
            ],
            agencyFeeSubtotal=217_500,
            agencyRevenueEstimate=217_500,
            lineItemSum=217_500,
            pricingFlags=[
                "[VERIFY: Mississippi state registration]",
                "[VERIFY: Named account manager bios required]",
            ],
        )
        reconciled = reconcile_proposal_budget(budget)
        assert_budget_canonical(reconciled)
        self.assertEqual(collect_pm_ratio_violations(reconciled), [])


if __name__ == "__main__":
    unittest.main()
