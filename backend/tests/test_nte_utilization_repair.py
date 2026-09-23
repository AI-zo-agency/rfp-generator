"""Partial-NTE disclosure + media-passthrough under-utilization (ledger-only)."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalResearchCache
from app.services.proposal_budget_content import ensure_partial_nte_scope_disclosure
from app.services.proposal_pricing_service import (
    _contract_horizon_block,
    budget_client_invoicing_total,
    budget_has_media_passthrough,
    budget_underutilizes_large_nte,
)


def _budget(**kw) -> ProposalBudget:
    kw.setdefault("rfpId", "r1")
    kw.setdefault("updatedAt", "2026-09-02T00:00:00Z")
    return ProposalBudget(**kw)


class PartialNteDisclosureTests(unittest.TestCase):
    def test_injects_disclosure_when_fees_far_under_nte(self) -> None:
        budget = _budget(
            rfpBudgetCap=950_000,
            agencyRevenueEstimate=77_000,
            agencyFeeSubtotal=77_000,
            lumpSumTotal=77_000,
            scopeSummary="zö agency structures fees as transparent project phases.",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Discovery & Plan Development",
                    unit="flat",
                    quantity=1,
                    rate=23_000,
                    extended=23_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-2",
                    category="labor",
                    description="Strategy & Audience Segmentation",
                    unit="flat",
                    quantity=1,
                    rate=25_500,
                    extended=25_500,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-3",
                    category="labor",
                    description="Creative Production & Toolkit",
                    unit="flat",
                    quantity=1,
                    rate=28_500,
                    extended=28_500,
                    lineItemType="agency_fee",
                ),
            ],
        )
        out = ensure_partial_nte_scope_disclosure(budget)
        self.assertIn("not a proposal against the full", out.scope_summary.casefold())
        self.assertIn("$950,000", out.scope_summary)
        self.assertIn("buyer-approved estimates", out.scope_summary)

    def test_idempotent_when_marker_present(self) -> None:
        budget = _budget(
            rfpBudgetCap=950_000,
            agencyRevenueEstimate=77_000,
            scopeSummary=(
                "These figures cover Discovery only — not a proposal against the full "
                "$950,000 NTE / contract ceiling."
            ),
        )
        out = ensure_partial_nte_scope_disclosure(budget)
        self.assertEqual(out.scope_summary, budget.scope_summary)


class UnderutilizationTests(unittest.TestCase):
    def test_agency_fee_only_does_not_force_fill(self) -> None:
        """Phase 1–3 only under NTE → disclosure path, not utilization repair."""
        budget = _budget(
            rfpBudgetCap=950_000,
            agencyRevenueEstimate=77_000,
            lumpSumTotal=77_000,
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Discovery",
                    unit="flat",
                    quantity=1,
                    rate=77_000,
                    extended=77_000,
                    lineItemType="agency_fee",
                )
            ],
        )
        self.assertFalse(budget_has_media_passthrough(budget))
        self.assertFalse(budget_underutilizes_large_nte(budget, rfp_text="anything"))
        self.assertAlmostEqual(budget_client_invoicing_total(budget), 77_000.0)

    def test_thin_media_passthrough_under_nte_triggers_repair_gate(self) -> None:
        budget = _budget(
            rfpBudgetCap=950_000,
            agencyRevenueEstimate=50_000,
            clientMediaPassthrough=100_000,
            totalClientInvoicing=150_000,
        )
        self.assertTrue(budget_has_media_passthrough(budget))
        self.assertTrue(budget_underutilizes_large_nte(budget))

    def test_full_nte_with_media_passthrough_ok(self) -> None:
        budget = _budget(
            rfpBudgetCap=950_000,
            agencyRevenueEstimate=200_000,
            clientMediaPassthrough=700_000,
            totalClientInvoicing=900_000,
        )
        self.assertFalse(budget_underutilizes_large_nte(budget))


class ContractHorizonBlockTests(unittest.TestCase):
    def test_injects_generic_horizon_from_plan(self) -> None:
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="2026-09-02T00:00:00Z",
            proposalExecutionPlan={
                "opportunity": {
                    "understanding": {
                        "timelineIntel": {
                            "contractHorizon": "base term through fixed calendar end",
                            "performanceEnd": "December 31, 2028",
                            "scheduleAuthority": "TBD after award — contractor proposes",
                        }
                    }
                }
            },
        )
        block = _contract_horizon_block(research)
        self.assertIn("CONTRACT / FUNDING HORIZON", block)
        self.assertIn("base term through fixed calendar end", block)
        self.assertIn("December 31, 2028", block)
        self.assertIn("contractor proposes", block)
        self.assertIn("any RFP", block)
        self.assertNotIn("Montana", block)
        self.assertNotIn("May 31", block)

    def test_empty_when_no_timeline_intel(self) -> None:
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="2026-09-02T00:00:00Z",
            proposalExecutionPlan={},
        )
        self.assertEqual(_contract_horizon_block(research), "")


if __name__ == "__main__":
    unittest.main()
