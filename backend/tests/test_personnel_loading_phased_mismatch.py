"""personnel_loading label + fixed phase lines must not ship a hollow hourly claim."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import render_budget_markdown
from app.services.proposal_pricing_service import coerce_budget_to_phased_from_guide


class PersonnelLoadingPhasedMismatchTests(unittest.TestCase):
    def _fixed_phase_budget(self) -> ProposalBudget:
        return ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    category="Production",
                    description="Quarterly magazine layout — four issues",
                    unit="page",
                    quantity=288,
                    rate=275.0,
                    extended=79200.0,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    category="Digital",
                    description="Digital flipbook platform",
                    unit="project",
                    quantity=1,
                    rate=2700.0,
                    extended=2700.0,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="3",
                    category="PM",
                    description="Annual production calendar & PM",
                    unit="year",
                    quantity=1,
                    rate=7500.0,
                    extended=7500.0,
                    lineItemType="agency_fee",
                ),
            ],
            qualifyingLanguage="zö agency works on a project-based fee schedule.",
            scopeSummary=(
                "Work outside this annual scope bills separately by the hour "
                "once the City approves a written estimate."
            ),
        )

    def test_coerce_flips_format_keeps_fixed_lines(self) -> None:
        budget = self._fixed_phase_budget()
        coerced, logs = coerce_budget_to_phased_from_guide(budget, None, rfp_text="")
        self.assertEqual(coerced.budget_format, "phased")
        self.assertEqual(len(coerced.line_items), 3)
        self.assertEqual(float(coerced.line_items[0].extended or 0), 79200.0)
        self.assertTrue(logs)

    def test_render_after_coerce_has_fee_detail_not_hollow_hourly_claim(self) -> None:
        budget = self._fixed_phase_budget()
        coerced, _ = coerce_budget_to_phased_from_guide(budget, None, rfp_text="")
        md = render_budget_markdown(coerced, rfp_text="")
        self.assertIn("Fee Detail by Phase", md)
        self.assertIn("$79,200", md)
        self.assertNotIn(
            "This table answers the RFP's scored Cost / hourly-rate instrument",
            md,
        )

    def test_render_defensive_coerce_when_format_still_personnel_loading(self) -> None:
        """Even if caller forgot coerce, render must not claim a missing hourly table."""
        budget = self._fixed_phase_budget()
        md = render_budget_markdown(budget, rfp_text="")
        self.assertIn("Fee Detail by Phase", md)
        self.assertNotIn(
            "This table answers the RFP's scored Cost / hourly-rate instrument",
            md,
        )


if __name__ == "__main__":
    unittest.main()
