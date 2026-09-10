"""Option Terms rebuild keeps fee / media / total distinct."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import (
    align_held_flat_option_year_line_items,
    rebuild_option_term_notes,
)


class OptionTermsDistinctTests(unittest.TestCase):
    def test_pass_through_fee_and_total_are_distinct(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="f",
                    category="Fees",
                    description="Professional fees",
                    extended=80_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="m",
                    category="Media",
                    description="Paid media",
                    extended=30_000,
                    lineItemType="client_passthrough",
                ),
            ],
            agencyRevenueEstimate=80_000,
            agencyFeeSubtotal=80_000,
            clientMediaPassthrough=30_000,
            totalClientInvoicing=110_000,
        )
        notes = rebuild_option_term_notes(budget)
        compact = notes.replace(",", "").replace(".00", "")
        self.assertIn("80000", compact)
        self.assertIn("30000", compact)
        self.assertIn("110000", compact)
        # Fee and total must both appear as distinct figures when media > 0.
        self.assertTrue(
            "Professional service fees" in notes or "agency commission revenue" in notes.casefold(),
            notes,
        )
        self.assertIn("pass-through", notes.casefold())
        self.assertIn("client invoicing", notes.casefold())
        # Must keep the closing paren / not truncate mid-sentence.
        self.assertIn("(at net, not", notes.casefold())
        self.assertNotIn("(at net.", notes)

    def test_held_flat_option_years_use_agency_fee(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="f",
                    category="Year 1",
                    description="Retainer + events",
                    extended=183_590,
                    lineItemType="agency_fee",
                ),
            ],
            agencyRevenueEstimate=183_590,
            agencyFeeSubtotal=183_590,
            clientMediaPassthrough=2_900,
            totalClientInvoicing=186_490,
            optionTermNotes="Option Year 2 and Option Year 3 held flat.",
        )
        notes = rebuild_option_term_notes(
            budget,
            rfp_context="The County may award Option Year 2 and Option Year 3.",
        )
        self.assertIn("183,590", notes)
        self.assertIn("held flat", notes.casefold())
        self.assertIn("Option Year 2", notes)
        self.assertIn("Option Year 3", notes)

    def test_aligns_option_year_line_items_to_base_fee(self) -> None:
        items = [
            BudgetLineItem(
                id="y1",
                category="Year 1",
                description="Agency fees",
                extended=183_590,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="y2",
                category="Option Year 2",
                description="same scope, pricing held flat",
                extended=189_255,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="y3",
                category="Option Year 3",
                description="same scope, pricing held flat",
                extended=189_255,
                lineItemType="agency_fee",
            ),
        ]
        aligned = align_held_flat_option_year_line_items(items)
        self.assertEqual(float(aligned[1].extended or 0), 183_590.0)
        self.assertEqual(float(aligned[2].extended or 0), 183_590.0)


if __name__ == "__main__":
    unittest.main()
