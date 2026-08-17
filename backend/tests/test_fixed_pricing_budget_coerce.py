"""Fixed-pricing RFPs must not ship hollow hourly role tables."""

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

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_format_judge import rfp_indicates_fixed_pricing_table
from app.services.proposal_pricing_service import coerce_budget_to_phased_from_guide


class FixedPricingDetectionTests(unittest.TestCase):
    def test_dane_county_style_fixed_pricing_table(self) -> None:
        rfp = (
            "The Cost Proposal section will either be incorporated into the system as a "
            "Pricing Table or as a separate Cost Proposal Attachment that must be downloaded, "
            "completed, saved, and uploaded to the Vendor Questionnaire section. "
            "Price(s) quoted shall include all labor, materials, equipment, shipping and other "
            "costs and conditions outlined within the bid. Pricing shall remain fixed."
        )
        self.assertTrue(rfp_indicates_fixed_pricing_table(rfp))

    def test_hourly_rfp_not_misclassified(self) -> None:
        rfp = "Provide fully burdened hourly rates for each labor category with Year-2 increases."
        self.assertFalse(rfp_indicates_fixed_pricing_table(rfp))


class CoercePhasedBudgetTests(unittest.TestCase):
    def _rate_card(self) -> PricingRateCard:
        return PricingRateCard(
            rates=[
                PricingRate(
                    rateId="guide-1.1-average",
                    service="Stakeholder Interviews",
                    tier="Average",
                    unit="fixed",
                    amount=7000.0,
                    amountLow=6000.0,
                    amountHigh=8000.0,
                    menuId="1.1",
                    confidence=0.95,
                ),
                PricingRate(
                    rateId="guide-2.1-average",
                    service="Messaging Framework",
                    tier="Average",
                    unit="fixed",
                    amount=7500.0,
                    amountLow=7500.0,
                    amountHigh=7500.0,
                    menuId="2.1",
                    confidence=0.95,
                ),
            ]
        )

    def test_coerces_hollow_personnel_loading_to_phased_fees(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Account Supervisor",
                    roleTitle="Account Supervisor",
                    unit="hours",
                    quantity=1,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-travel",
                    category="travel",
                    description="Travel",
                    unit="flat",
                    quantity=1,
                    rate=8500.0,
                    extended=8500.0,
                    lineItemType="direct_expense",
                ),
            ],
        )
        rfp = (
            "Pricing Table or Cost Proposal Attachment uploaded to Vendor Questionnaire. "
            "Pricing shall remain fixed."
        )
        coerced, logs = coerce_budget_to_phased_from_guide(
            budget, self._rate_card(), rfp_text=rfp
        )
        self.assertTrue(logs)
        self.assertEqual(coerced.budget_format, "phased")
        self.assertTrue(any(float(i.extended or 0) > 0 for i in coerced.line_items))
        self.assertTrue(any(i.extended == 8500.0 for i in coerced.line_items))
        self.assertFalse(any((i.role_title or "") == "Account Supervisor" for i in coerced.line_items))


if __name__ == "__main__":
    unittest.main()
