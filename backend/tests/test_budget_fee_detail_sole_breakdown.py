"""Fee Detail by Phase is the only fee breakdown — no Component|Share twin table."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    format_qualifying_language_for_client,
    render_budget_markdown,
    scrub_duplicate_budget_breakdown_tables,
)


def _phased_budget() -> ProposalBudget:
    return ProposalBudget(
        rfpId="gilroy",
        updatedAt="2026-01-01T00:00:00Z",
        budgetFormat="phased",
        lumpSumTotal=122000,
        agencyRevenueEstimate=122000,
        qualifyingLanguage=(
            "### Investment Framing\n\n"
            "| Component | Share | Amount | Notes |\n"
            "| --- | ---: | ---: | --- |\n"
            "| Sponsorship & B2B | 3% | $4,200 | |\n"
            "| Creative | 3% | $3,600 | |\n"
            "| Digital Execution | 34% | $41,800 | |\n"
            "| Digital | 4% | $5,000 | |\n"
            "| Digital (2) | 1% | $1,200 | |\n"
            "| Strategy | 6% | $7,000 | |\n"
            "| Strategy (2) | 4% | $4,500 | |\n"
        ),
        lineItems=[
            BudgetLineItem(
                id="p1",
                category="Sponsorship & B2B",
                description="Phase 1 Sponsorship & B2B (RFP §3A)",
                extended=9000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="p2",
                category="Digital Execution",
                description="Phase 2 Digital Execution (RFP §3D)",
                extended=46800,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="p3",
                category="Strategy",
                description="Phase 3 Strategy (RFP §3B)",
                extended=11500,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="p4",
                category="Other",
                description="Phase 4 Remaining delivery",
                extended=54700,
                lineItemType="agency_fee",
            ),
        ],
    )


class FeeDetailSoleBreakdownTests(unittest.TestCase):
    def test_render_drops_component_share_when_fee_detail_present(self) -> None:
        md = render_budget_markdown(_phased_budget())
        self.assertIn("## Fee Detail by Phase", md)
        self.assertIn("| Phase | Scope | Fee |", md)
        self.assertNotIn("| Component | Share | Amount |", md)
        self.assertNotIn("Strategy (2)", md)
        self.assertIn("$122000", md.replace(",", "").replace(" ", ""))
        self.assertIn("Sponsorship & B2B", md)
        self.assertIn("$9000", md.replace(",", ""))

    def test_scrub_removes_conflicting_mix_table_from_persisted_section(self) -> None:
        body = (
            "## Terms\n\n"
            "### Investment Framing\n\n"
            "| Component | Share | Amount | Notes |\n"
            "| --- | ---: | ---: | --- |\n"
            "| Sponsorship & B2B | 3% | $4,200 | |\n"
            "| Creative | 3% | $3,600 | |\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Sponsorship & B2B | RFP §3A | $9,000 |\n"
            "| **Total** | | **$122,000** |\n"
        )
        out, logs = scrub_duplicate_budget_breakdown_tables(body)
        self.assertIn("## Fee Detail by Phase", out)
        self.assertIn("$9,000", out)
        self.assertNotIn("| Component | Share | Amount |", out)
        self.assertNotIn("$4,200", out)
        self.assertTrue(logs)

    def test_suppress_mix_keeps_investment_framing_bullets_only(self) -> None:
        wall = (
            "Investment Framing\n"
            "zö agency prices this work as defined project phases.\n"
            "Sponsorship accounts for 3% ($4,200). Creative is 3% ($3,600).\n"
            "Scope Protection\n"
            "Fees reflect the scope at proposal stage.\n"
        )
        out = format_qualifying_language_for_client(wall, suppress_mix_tables=True)
        self.assertNotIn("| Component | Share | Amount |", out)
        self.assertIn("### Investment Framing", out)
        self.assertIn("### Scope Protection", out)
        self.assertNotIn("$4,200", out)


if __name__ == "__main__":
    unittest.main()
