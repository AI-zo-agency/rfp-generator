"""Opportunity hard constraints → Cost / SOW / Timeline writers."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    ensure_fee_detail_table_in_budget_markdown,
    render_budget_markdown,
    _rollup_phase_fee_rows,
)
from app.services.proposal_opportunity_constraints import (
    budget_format_omits_fee_detail,
    format_opportunity_hard_constraints,
    opportunity_pricing_format_hint,
)


def _dupage_plan() -> dict:
    """Minimal slice matching workNet DuPage research (dual NTE + Pricing Form)."""
    return {
        "opportunity": {
            "understanding": {
                "client": 'DuPage County (Workforce Development Division / "workNet DuPage")',
                "budgetIntel": {
                    "notes": (
                        "Pricing scored separately by Procurement Division (15 of 100 points); "
                        "Bidder proposes hourly rate and, for the Youth Campaign, estimated hours."
                    ),
                    "ceiling": None,
                },
                "timelineIntel": {
                    "contractHorizon": (
                        "One (1) year base term with up to three (3) additional one-year renewals"
                    ),
                    "performanceEnd": (
                        "Base term completion date 10/31/2027 "
                        "(anticipated start 11/1/2026)"
                    ),
                },
            },
            "scope": {
                "mandatory": [
                    "Provide two separate sets of deliverables: (1) General workNet DuPage "
                    "marketing and communications, and (2) Young adult outreach campaign",
                    "Manage Social Media channels — approximately 4 posts per month across "
                    "LinkedIn and Meta",
                    "Produce approximately 2-4 videos annually (3-5 minutes)",
                    "Submit an Original Signed Proposal in PDF format",
                    "Proposals will be received for the Contract before October 2, 2026",
                ],
                "notes": (
                    "Scope is split into two distinct budget-capped tracks: "
                    "General Marketing/Communications ($75,000 annual not-to-exceed) and "
                    "Young Adult Outreach Campaign ($100,000 annual not-to-exceed), "
                    "each priced on an hourly basis."
                ),
                "optional": [
                    "Occasional emails on an as-needed basis (e.g., Rapid Response)"
                ],
                "dependencies": [
                    "Final priorities established through an approved communications plan"
                ],
            },
            "compliance": {
                "items": [
                    {
                        "requirement": "Submit Proposal Pricing Form as a separate email attachment",
                        "mandatory": True,
                    }
                ]
            },
        },
        "delivery": {
            "budget": {
                "pricingModel": "T&M",
                "ceiling": (
                    "General Marketing track: $75,000 annual not-to-exceed; "
                    "Young Adult Outreach Campaign track: $100,000 annual not-to-exceed"
                ),
                "constraints": [
                    "Two separate annual NTE budgets must not be commingled",
                ],
            }
        },
    }


class OpportunityConstraintsTests(unittest.TestCase):
    def test_dupage_format_hint_is_blended_rate_form(self) -> None:
        hint = opportunity_pricing_format_hint(_dupage_plan())
        self.assertEqual(hint, "blended_rate_form")

    def test_budget_block_includes_caps_sow_timeline_not_admin(self) -> None:
        block = format_opportunity_hard_constraints(_dupage_plan(), focus="budget")
        self.assertIn("OPPORTUNITY HARD CONSTRAINTS", block)
        self.assertIn("$75,000", block)
        self.assertIn("$100,000", block)
        self.assertIn("LinkedIn and Meta", block)
        self.assertIn("2-4 videos", block)
        self.assertIn("10/31/2027", block)
        self.assertIn("blended_rate_form", block)
        self.assertIn("Proposal Pricing Form", block)
        self.assertNotIn("Submit an Original Signed Proposal", block)
        self.assertNotIn("Proposals will be received", block)

    def test_sow_focus_keeps_deliverables(self) -> None:
        block = format_opportunity_hard_constraints(_dupage_plan(), focus="sow")
        self.assertIn("workNet DuPage", block)
        self.assertIn("Rapid Response", block)
        self.assertNotIn("REQUIRED budgetFormat", block)

    def test_timeline_focus_keeps_horizon(self) -> None:
        block = format_opportunity_hard_constraints(_dupage_plan(), focus="timeline")
        self.assertIn("10/31/2027", block)
        self.assertIn("one-year renewals", block)

    def test_fee_detail_omitted_for_form_format(self) -> None:
        self.assertTrue(budget_format_omits_fee_detail("blended_rate_form"))
        self.assertTrue(budget_format_omits_fee_detail("personnel_loading"))
        self.assertFalse(budget_format_omits_fee_detail("phased"))

    def test_ensure_fee_detail_strips_when_form_format(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        budget = ProposalBudget(
            rfpId="rfp-test",
            updatedAt=now,
            budgetFormat="blended_rate_form",
            formHourlyRate=175.0,
            lineItems=[
                BudgetLineItem(
                    id="1",
                    category="Part 1",
                    description="General marketing",
                    unit="hour",
                    rate=175,
                    quantity=100,
                    extended=17500,
                )
            ],
        )
        body = (
            "## Proposed Investment\n\n**Total: $17,500**\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Scope | Fee |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Invented | $13,500 |\n\n"
            "## Terms\n\nFirm fixed.\n"
        )
        out = ensure_fee_detail_table_in_budget_markdown(body, budget)
        self.assertNotIn("Fee Detail by Phase", out)
        self.assertIn("## Terms", out)

    def test_rollup_does_not_emit_plus_n_more(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        items = [
            BudgetLineItem(
                id=str(i),
                category="Implementation",
                description=f"Deliverable {i} for the County engagement",
                extended=1000.0 * (i + 1),
                rate=1000.0 * (i + 1),
                quantity=1,
            )
            for i in range(5)
        ]
        budget = ProposalBudget(rfpId="rfp-test", updatedAt=now, lineItems=items)
        rows = _rollup_phase_fee_rows(budget)
        self.assertEqual(len(rows), 1)
        _phase, scope, _amt = rows[0]
        self.assertNotIn("Plus", scope)
        self.assertNotIn("more.", scope)
        self.assertIn("Deliverable 0", scope)
        self.assertIn("Deliverable 4", scope)

    def test_render_form_format_skips_fee_detail_heading(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        budget = ProposalBudget(
            rfpId="rfp-test",
            updatedAt=now,
            budgetFormat="blended_rate_form",
            formHourlyRate=150.0,
            formMonthlyRate=None,
            formAnnualRate=None,
            lumpSumTotal=15000.0,
            agencyRevenueEstimate=15000.0,
            lineItems=[
                BudgetLineItem(
                    id="1",
                    category="Fees",
                    description="Part 1 general",
                    unit="hour",
                    rate=150,
                    quantity=100,
                    extended=15000,
                )
            ],
            qualifyingLanguage="Hourly rates as stated on the Proposal Pricing Form.",
        )
        md = render_budget_markdown(budget, rfp_text="Submit the Proposal Pricing Form.")
        self.assertNotIn("Fee Detail by Phase", md)
        self.assertNotIn("Supporting Fee Detail", md)


if __name__ == "__main__":
    unittest.main()
