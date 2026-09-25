"""Tests for deterministic budget reconciliation and validation."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_validation import (
    collect_one_time_recurring_violations,
)


class ProposalBudgetValidationTests(unittest.TestCase):
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
