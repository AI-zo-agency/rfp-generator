"""Option Terms rebuild keeps fee / media / total distinct."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import rebuild_option_term_notes


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


if __name__ == "__main__":
    unittest.main()
