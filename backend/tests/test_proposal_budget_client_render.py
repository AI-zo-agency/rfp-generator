"""Client-facing budget render: one total, phase fees, no travel double-bill."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem


class QualifyingLanguageFormatTests(unittest.TestCase):
    def test_investment_mix_paragraph_becomes_table(self) -> None:
        from app.services.proposal_budget_content import (
            format_qualifying_language_for_client,
        )

        wall = (
            "Investment Framing\n"
            "Year 1 is a project-based fee schedule totaling $73,621 "
            "($33,081 in professional fees plus $40,540 in client media "
            "pass-through). Placed Media Buys account for 55% ($40,540) "
            "invoiced at net cost. Media Commission is 8% ($6,081) covering "
            "strategy, negotiation, trafficking, and optimization. Formative "
            "Research and Strategic Planning is 16% ($11,500). Creative "
            "Development is 9% ($6,500). Project Management is 10% ($7,500). "
            "Evaluation is 2% ($1,500). Combined Media Planning, Placement, "
            "and Optimization is 63% ($46,621).\n"
        )
        out = format_qualifying_language_for_client(wall)
        self.assertIn("### Investment Framing", out)
        self.assertIn("| Component | Share | Amount |", out)
        self.assertIn("| Placed Media Buys | 55% | $40,540 |", out)
        self.assertIn("| Media Commission | 8% | $6,081 |", out)
        self.assertIn("| Evaluation | 2% | $1,500 |", out)
        self.assertNotIn(
            "Placed Media Buys account for 55% ($40,540) invoiced at net cost. "
            "Media Commission is 8%",
            out,
        )
        self.assertLess(max(len(p) for p in out.split("\n")), 220)

    def test_stacked_bullets_and_pct_first_mix_become_table(self) -> None:
        from app.services.proposal_budget_content import (
            format_qualifying_language_for_client,
        )

        messy = (
            "Investment Framing\n"
            "- - - zö agency works on a project-based fee schedule.\n"
            "- - - The Year 1 investment of $68,200 reflects the scope as "
            "understood at proposal stage, with 59% allocated to placed media "
            "($40,540 client pass-through at net + $6,081 agency commission), "
            "26% to formative research and strategic planning ($11,500), "
            "10% to creative development ($6,500), 3% to project management "
            "($2,079), and 2% to evaluation ($1,500).\n"
            "Scope Protection\n"
            "- - - Pricing reflects the scope as understood at proposal stage.\n"
        )
        once = format_qualifying_language_for_client(messy)
        twice = format_qualifying_language_for_client(once)
        self.assertNotIn("- -", once)
        self.assertEqual(once, twice)
        self.assertIn("| Component | Share | Amount |", once)
        self.assertIn("| formative research and strategic planning | 26% | $11,500 |", once)
        self.assertIn("| creative development | 10% | $6,500 |", once)
        self.assertIn("### Scope Protection", once)
        self.assertIn("- Pricing reflects the scope", once)

    def test_ledger_mix_table_replaces_invented_percentages(self) -> None:
        from app.services.proposal_budget_content import (
            format_qualifying_language_for_client,
        )

        wall = (
            "Investment Framing\n"
            "The Year 1 investment of $68,200 reflects the scope, with 3% to "
            "project management ($2,079) and 2% to evaluation ($1,500).\n"
        )
        items = [
            BudgetLineItem(
                id="pm",
                category="Account & Project Management",
                description="Phase 5 Project Management",
                extended=7500,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="eval",
                category="Measurement & Reporting",
                description="Phase 6 Campaign Evaluation",
                extended=1500,
                lineItemType="agency_fee",
            ),
        ]
        out = format_qualifying_language_for_client(
            wall, line_items=items, total=9000
        )
        self.assertIn("| Account & Project Management | 83% | $7,500 |", out)
        self.assertNotIn("$2,079", out)
        self.assertIn("$7,500", out)


if __name__ == "__main__":
    unittest.main()
