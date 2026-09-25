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
from app.services.proposal_budget_playbook import (
    apply_budget_freeform_postprocess,
    refuse_noncompliant_budget_edit,
)


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

    def test_postprocess_drops_mix_table(self) -> None:
        body = (
            "### Investment Framing\n\n"
            "| Component | Share | Amount | Notes |\n"
            "| --- | ---: | ---: | --- |\n"
            "| Creative | 50% | $3500 | |\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Interviews | $7000 |\n"
        )
        out, logs = apply_budget_freeform_postprocess(body, budget=_budget())
        self.assertNotIn("| Component | Share | Amount |", out)
        self.assertIn("## Fee Detail by Phase", out)
        self.assertTrue(logs)

    def test_postprocess_restores_stripped_hourly_schedule(self) -> None:
        prior = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Interviews | $7000 |\n\n"
            "## Hourly Rate Schedule by Classification\n\n"
            "| Role / Labor Category | Hourly Rate (billable) | Year-2 % Increase |\n"
            "| --- | ---: | ---: |\n"
            "| Account Manager | $275 | — |\n"
            "| Agency Director | $400 | — |\n"
        )
        wiped = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Interviews | $7000 |\n\n"
            "## Hourly Rate Schedule by Classification\n\n"
            "Confirm before submit — complete hourly rate schedule by classification.\n"
        )
        out, logs = apply_budget_freeform_postprocess(
            wiped, budget=_budget(), prior_text=prior
        )
        self.assertTrue(any("Hourly Rate Schedule" in x for x in logs))
        self.assertIn("| Account Manager | $275 |", out)
        self.assertIn("| Agency Director | $400 |", out)
        self.assertNotIn("complete hourly rate schedule by classification", out.casefold())

    def test_normalize_hourly_schedule_strips_manual_fill_name_cells(self) -> None:
        from app.models.proposal import VerifiedRate

        body = (
            "## Hourly Rate Schedule by Classification\n\n"
            "| Role / Labor Category | Team Member Name | Hourly Rate (billable) | Year-2 % Increase | Year-3 % Increase |\n"
            "| --- | --- | ---: | ---: | ---: |\n"
            "| Account Manager | Jax Lai / [MANUAL FILL: fabricated name removed] | $275 | | |\n"
            "| Team member on this engagement | Sonja Anderson | $400 |  |  |\n"
            "| Art Director | [MANUAL FILL: name not in KB org chart] | $275 | | |\n"
        )
        budget = _budget()
        budget = budget.model_copy(
            update={
                "verified_rates": [
                    VerifiedRate(
                        personName="", role="Account Manager", hourlyRate=275, source="x"
                    ),
                    VerifiedRate(
                        personName="", role="Agency Director", hourlyRate=400, source="x"
                    ),
                    VerifiedRate(
                        personName="", role="Art Director", hourlyRate=275, source="x"
                    ),
                ]
            }
        )
        out, logs = apply_budget_freeform_postprocess(body, budget=budget, prior_text=body)
        self.assertTrue(logs)
        self.assertIn("| Account Manager | — | $275 | — | — |", out)
        self.assertIn("| Agency Director | Sonja Anderson | $400 | — | — |", out)
        self.assertIn("| Art Director | — | $275 | — | — |", out)
        self.assertNotIn("MANUAL FILL", out)
        self.assertNotIn("Team member on this engagement", out)
        self.assertNotIn("Jax Lai", out)


if __name__ == "__main__":
    unittest.main()
