"""Tests for deterministic budget reconciliation and validation."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import (
    adjust_pm_line_items_to_guide,
    assert_budget_canonical,
    collect_one_time_recurring_violations,
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

    def test_sonja_line_flags_do_not_block_validation(self) -> None:
        budget = ProposalBudget(
            rfpId="test-rfp",
            updatedAt="2026-07-17T00:00:00Z",
            lineItems=[
                _line(item_id="a", description="Discovery bundle", extended=50_000),
            ],
            agencyFeeSubtotal=50_000,
            agencyRevenueEstimate=50_000,
            lineItemSum=50_000,
            pricingFlags=[
                "L01 — Bundled Discovery exceeds Average ceiling; Sonja review required",
                "L16 — Bundled media spend outside standard 00_Guide_Pricing menu",
                "Verified burdened hourly rates are internal benchmarks — Sonja review",
            ],
        )
        reconciled = reconcile_proposal_budget(budget)
        assert_budget_canonical(reconciled)

    def test_strips_yearly_allocation_envelope_rows_and_respects_hard_cap(self) -> None:
        """HTA-style bug: Year 1/2/3 allocations must not be summed with work line items."""
        budget = ProposalBudget(
            rfpId="hta-budget-cap",
            updatedAt="2026-07-21T00:00:00Z",
            rfpBudgetCap=2_950_000,
            lineItems=[
                _line(item_id="discovery", description="Discovery & research", extended=200_000),
                _line(item_id="strategy", description="Campaign strategy", extended=300_000),
                _line(item_id="content", description="Content creation", extended=250_000),
                _line(item_id="digital", description="Digital marketing", extended=233_631.58),
                _line(
                    item_id="y1",
                    description="Annual Allocation Year 1",
                    extended=898_000,
                    category="Funding",
                ),
                _line(
                    item_id="y2",
                    description="Annual Allocation Year 2",
                    extended=648_000,
                    category="Funding",
                ),
                _line(
                    item_id="y3",
                    description="Annual Allocation Year 3",
                    extended=1_399_000,
                    category="Funding",
                ),
            ],
            agencyRevenueEstimate=3_933_631.58,
            lineItemSum=3_933_631.58,
        )
        reconciled = reconcile_proposal_budget(budget)
        assert_budget_canonical(reconciled)
        self.assertLessEqual(float(reconciled.agency_revenue_estimate or 0), 2_950_000.01)
        self.assertAlmostEqual(float(reconciled.agency_revenue_estimate or 0), 983_631.58, places=2)
        descs = " ".join(i.description for i in reconciled.line_items).lower()
        self.assertNotIn("annual allocation", descs)
        self.assertTrue(
            any("envelope row" in f.lower() or "allocation" in f.lower() for f in reconciled.pricing_flags)
        )

    def test_scales_over_cap_work_lines_to_hard_cap(self) -> None:
        budget = ProposalBudget(
            rfpId="cap-scale",
            updatedAt="2026-07-21T00:00:00Z",
            rfpBudgetCap=100_000,
            lineItems=[
                _line(item_id="a", description="Strategy package", extended=80_000),
                _line(item_id="b", description="Content package", extended=40_000),
            ],
            agencyRevenueEstimate=120_000,
            lineItemSum=120_000,
        )
        reconciled = reconcile_proposal_budget(budget)
        assert_budget_canonical(reconciled)
        self.assertLessEqual(float(reconciled.agency_revenue_estimate or 0), 100_000.01)

    def test_pm_high_ratio_not_auto_cut_below_guide_floor(self) -> None:
        """SRIA-style: cutting PM to hit 5–8% must not drop below ~$7,500 engagement floor."""
        items = [
            _line(item_id="fees", description="Strategy and creative bundle", extended=20_000),
            _line(
                item_id="pm",
                description="Project management — annual account",
                extended=6_675,
                category="Account & Project Management",
            ),
        ]
        adjusted, note = adjust_pm_line_items_to_guide(items, agency_base=26_675)
        self.assertIsNotNone(note)
        self.assertIn("do not auto-cut", note.lower())
        pm_total = sum(float(i.extended or 0) for i in adjusted if "project management" in i.description.lower())
        self.assertGreaterEqual(pm_total, 6_675 - 0.01)

    def test_one_time_setup_times_twelve_flags_violation(self) -> None:
        budget = ProposalBudget(
            rfpId="sria-email",
            updatedAt="2026-07-21T00:00:00Z",
            lineItems=[
                BudgetLineItem(
                    id="email-setup",
                    category="Content Creation",
                    description="Email Newsletter Design & Setup",
                    quantity=12,
                    rate=1_200,
                    extended=14_400,
                    lineItemType="agency_fee",
                    unit="months",
                ),
            ],
            agencyRevenueEstimate=14_400,
            lineItemSum=14_400,
        )
        violations = collect_one_time_recurring_violations(budget)
        self.assertTrue(violations)
        self.assertIn("email-setup", violations[0])


if __name__ == "__main__":
    unittest.main()
