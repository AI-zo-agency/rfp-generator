"""Cost tab chat: narrative freeform allowed; fee mutations stay canonical."""

from __future__ import annotations

import sys
import types
import unittest

if "langchain_openai" not in sys.modules:
    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # pragma: no cover
        pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalSection
from app.services.proposal_budget_playbook import refuse_noncompliant_budget_edit


def _budget() -> ProposalBudget:
    return ProposalBudget(
        rfpId="r1",
        updatedAt="t",
        budgetFormat="phased",
        lineItems=[
            BudgetLineItem(
                id="li-1",
                category="labor",
                description="1.1 Stakeholder Interviews",
                unit="flat",
                quantity=1,
                rate=7000,
                extended=7000,
                lineItemType="agency_fee",
            )
        ],
        agencyRevenueEstimate=7000,
        lumpSumTotal=7000,
    )


class BudgetFreeformIntentTests(unittest.TestCase):
    def test_refuse_invented_dollars(self) -> None:
        prior = "## Fee Detail\n| Phase | Fee |\n| A | $7000 |\n"
        new = prior + "\nAlso add contingency $45000.\n"
        msg = refuse_noncompliant_budget_edit(
            "improve writing",
            new,
            prior_text=prior,
            budget=_budget(),
        )
        self.assertIsNotNone(msg)
        self.assertIn("45000", msg or "")

    def test_refuse_skips_non_budget_sections(self) -> None:
        """Exec Summary / voice edits must not be blocked by the Cost fee ledger."""
        prior = "Stewardship for Gilroy — keep the foundation intact."
        new = (
            prior
            + " Modernize sponsorship collateral so a $2,500 tier and a "
            "$25,000+ tier read clearly."
        )
        exec_sum = ProposalSection(
            id="section-1",
            title="Executive Summary",
            content=prior,
            status="generated",
        )
        self.assertIsNone(
            refuse_noncompliant_budget_edit(
                "Make it bit descriptive more with zo agency voice",
                new,
                prior_text=prior,
                budget=_budget(),
                section=exec_sum,
            )
        )
        cost = ProposalSection(
            id="section-cost",
            title="Cost Proposal",
            content=prior,
            status="generated",
        )
        self.assertIsNotNone(
            refuse_noncompliant_budget_edit(
                "improve writing",
                new,
                prior_text=prior,
                budget=_budget(),
                section=cost,
            )
        )

if __name__ == "__main__":
    unittest.main()
