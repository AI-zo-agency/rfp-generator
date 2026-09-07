"""Pricing Guide USE VERBATIM Terms must ship on Build Proposal / Cost chat."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    PRICING_GUIDE_VERBATIM_INVESTMENT_FRAMING,
    PRICING_GUIDE_VERBATIM_REIMBURSABLE_EXPENSES,
    ensure_pricing_guide_verbatim_in_budget_markdown,
    force_pricing_guide_verbatim_qualifying_language,
    prepare_budget_for_client_display,
    qualifying_language_has_pricing_guide_verbatim,
    render_budget_markdown,
)
from app.services.proposal_budget_playbook import apply_budget_freeform_postprocess


class PricingGuideVerbatimTests(unittest.TestCase):
    def test_force_keeps_additive_reimbursable_note(self) -> None:
        paraphrased = (
            "### Investment Framing\n\nShort rewrite without abide line.\n\n"
            "### Scope Protection\n\nMissing discovery sentence.\n\n"
            "### Reimbursable Expenses\n\n"
            "Cashless platforms and wayfinding tools billed at cost with prior approval.\n\n"
            "### Revision Rounds\n\nTwo rounds only."
        )
        out = force_pricing_guide_verbatim_qualifying_language(paraphrased)
        self.assertTrue(qualifying_language_has_pricing_guide_verbatim(out))
        self.assertIn(PRICING_GUIDE_VERBATIM_INVESTMENT_FRAMING, out)
        self.assertIn(PRICING_GUIDE_VERBATIM_REIMBURSABLE_EXPENSES, out)
        self.assertIn("Cashless platforms and wayfinding tools", out)

    def test_prepare_budget_forces_verbatim(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            lineItems=[
                BudgetLineItem(
                    id="p1",
                    category="Discovery",
                    description="Kickoff",
                    lineItemType="agency_fee",
                    quantity=1,
                    rate=1000,
                    extended=1000,
                )
            ],
            qualifyingLanguage="### Investment Framing\n\nParaphrased fluff only.",
            lumpSumTotal=1000,
            agencyRevenueEstimate=1000,
            totalClientInvoicing=1000,
        )
        cleaned = prepare_budget_for_client_display(budget)
        self.assertTrue(
            qualifying_language_has_pricing_guide_verbatim(
                cleaned.qualifying_language or ""
            )
        )
        md = render_budget_markdown(cleaned)
        self.assertIn("we abide by those terms as does our client", md.casefold())
        self.assertIn("mileage at current irs rate", md.casefold())

    def test_freeform_postprocess_restores_terms(self) -> None:
        body = (
            "## Proposed Investment\n\n**Total proposed investment: $1,000.00**\n\n"
            "## Terms\n\n"
            "### Investment Framing\n\nParaphrase.\n\n"
            "### Reimbursable Expenses\n\nOnly festival tech fees.\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Kickoff | $1,000 |\n"
        )
        out, logs = apply_budget_freeform_postprocess(body)
        self.assertTrue(qualifying_language_has_pricing_guide_verbatim(out))
        self.assertTrue(any("USE VERBATIM" in line for line in logs))

    def test_markdown_helper_idempotent_when_already_verbatim(self) -> None:
        ql = force_pricing_guide_verbatim_qualifying_language("")
        body = f"## Terms\n\n{ql}\n\n## Fee Detail by Phase\n\n| A | B |\n"
        again = ensure_pricing_guide_verbatim_in_budget_markdown(body)
        self.assertIn("we abide by those terms", again.casefold())


if __name__ == "__main__":
    unittest.main()
