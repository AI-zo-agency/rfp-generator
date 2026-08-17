"""Client-facing budget prose must not leak internal guide references."""

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

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    _scrub_internal_budget_jargon,
    render_budget_markdown,
)


class ClientBudgetProseTests(unittest.TestCase):
    def test_scrub_removes_guide_and_manual_fill(self) -> None:
        raw = (
            "Rates follow zö's Industry Low pricing guide for municipal work. "
            "Fixed-fee pricing per 00_Guide_Pricing (Industry Low tier). "
            "*Rates are fully burdened agency work rates from 00_Guide_Pricing where bound; "
            "blank cells are MANUAL FILL — never invent a named-person $/hr.*"
        )
        cleaned = _scrub_internal_budget_jargon(raw)
        self.assertNotIn("00_Guide_Pricing", cleaned)
        self.assertNotIn("blank cells are MANUAL FILL", cleaned.casefold())
        self.assertNotIn("pricing guide", cleaned.casefold())

    def test_render_budget_markdown_preserves_manual_fill_rows(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="phased",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="Media",
                    description="[MANUAL FILL: annual client media pass-through — confirm before submission]",
                    unit="flat",
                    extended=None,
                ),
                BudgetLineItem(
                    id="li-2",
                    category="labor",
                    description="1.1 Stakeholder Interviews",
                    unit="flat",
                    extended=7000,
                ),
            ],
            agencyRevenueEstimate=7000,
            lumpSumTotal=7000,
        )
        md = render_budget_markdown(budget, rfp_text="Pricing shall remain fixed.")
        self.assertIn("[MANUAL FILL: annual client media pass-through", md)
        self.assertNotIn("| Media | [ |", md)

    def test_render_budget_markdown_is_client_clean(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="phased",
            pricingTier="Low",
            scopeSummary="Fixed-fee pricing per 00_Guide_Pricing (Industry Low tier).",
            qualifyingLanguage=(
                "Investment framing per 00_Guide_Pricing and Industry Low pricing guide."
            ),
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
        md = render_budget_markdown(budget, rfp_text="Pricing shall remain fixed. Pricing Table.")
        self.assertNotIn("00_Guide_Pricing", md)
        self.assertNotIn("Industry Low pricing guide", md)
        self.assertIn("Stakeholder Interviews", md)
        self.assertNotIn("| labor |", md)
        self.assertIn("Discovery & Research", md)


if __name__ == "__main__":
    unittest.main()
