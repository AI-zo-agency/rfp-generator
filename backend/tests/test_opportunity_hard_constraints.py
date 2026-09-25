"""Opportunity hard constraints → Cost / SOW / Timeline writers."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models.delivery_constraints import (
    DeliveryConstraints,
    DeliveryHorizon,
    DeliveryTrack,
)
from app.models.pricing_instrument import PricingInstrument
from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    render_budget_markdown,
)
from app.services.proposal_opportunity_constraints import (
    format_opportunity_hard_constraints,
)

_DUPAGE_INSTRUMENT = (
    Path(__file__).resolve().parent / "fixtures" / "dupage_pricing_instrument.json"
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
    def test_budget_block_includes_caps_sow_timeline_not_admin(self) -> None:
        block = format_opportunity_hard_constraints(_dupage_plan(), focus="budget")
        self.assertIn("OPPORTUNITY HARD CONSTRAINTS", block)
        self.assertIn("$75,000", block)
        self.assertIn("$100,000", block)
        self.assertIn("LinkedIn and Meta", block)
        self.assertIn("2-4 videos", block)
        self.assertIn("10/31/2027", block)
        self.assertNotIn("REQUIRED budgetFormat", block)
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


    def test_phased_instrument_does_not_emit_bid_number(self) -> None:
        """phased_fee_schedule must not paint Bid Number / Part rows via Cost render."""
        now = datetime.now(timezone.utc).isoformat()
        budget = ProposalBudget(
            rfpId="rfp-phased",
            updatedAt=now,
            budgetFormat="phased",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    category="Discovery",
                    description="Kickoff and research",
                    unit="flat",
                    rate=12000,
                    quantity=1,
                    extended=12000,
                )
            ],
            agencyRevenueEstimate=12000.0,
            lumpSumTotal=12000.0,
        )
        inst = PricingInstrument(
            kind="phased_fee_schedule",
            bidNumber="26-088-WIOA",
            confidence=0.8,
        )
        md = render_budget_markdown(budget, pricing_instrument=inst)
        self.assertNotIn("BID NUMBER", md)
        self.assertNotIn("Bid Number", md)
        self.assertNotIn("26-088-WIOA", md)
        self.assertNotIn("Part 1", md)
        self.assertNotIn("Part 2", md)

    def test_typed_delivery_block_preferred_over_freeform_scope(self) -> None:
        """When typed packs are on the plan, hard-constraints prefer them."""
        plan = _dupage_plan()
        plan["pricingInstrument"] = json.loads(_DUPAGE_INSTRUMENT.read_text())
        plan["deliveryConstraints"] = DeliveryConstraints(
            tracks=[
                DeliveryTrack(
                    id="part-1",
                    label="General Marketing",
                    nteAnnual=75000,
                    billing="hourly",
                ),
                DeliveryTrack(
                    id="part-2",
                    label="Young Adult Outreach",
                    nteAnnual=100000,
                    billing="hourly",
                ),
            ],
            mandatoryDeliverables=[
                "Typed-only Young Adult Outreach Campaign",
            ],
            outOfScope=["County-wide website redesign"],
            nonCommingleTracks=True,
            horizon=DeliveryHorizon(
                baseTerm="1 year",
                renewals="up to 3",
                maxTerm="4 years",
            ),
        ).model_dump(by_alias=True)

        block = format_opportunity_hard_constraints(plan, focus="all")
        self.assertIn("Typed instrument.kind: buyer_pricing_form", block)
        self.assertIn("Typed tracks / NTEs (authoritative — do not merge)", block)
        self.assertIn("Typed delivery: tracks must not be commingled", block)
        self.assertIn("typed DeliveryConstraints", block)
        self.assertIn("Typed-only Young Adult Outreach Campaign", block)
        # Freeform scope mandatory that is not in typed pack must not win.
        self.assertNotIn("Manage Social Media channels", block)


if __name__ == "__main__":
    unittest.main()
