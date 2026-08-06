"""Scan RFP regenerates Phase 3.5 budget when the whole budget is missing."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_fulfill_rfp_budget_kpi import (
    manuscript_budget_is_missing,
    manuscript_cost_section_is_hollow,
    pricing_model_lacks_professional_fees,
    run_fulfill_budget_scan,
)


def _rfp(rfp_id: str = "rfp-regen") -> RfpRecord:
    return RfpRecord(
        id=rfp_id,
        title="T",
        client="C",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="n",
    )


class ManuscriptBudgetMissingTests(unittest.TestCase):
    def test_missing_when_no_research_budget(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-regen",
            sections=[
                ProposalSection(
                    id="sec-1",
                    title="Approach",
                    content="We will deliver the work.",
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        self.assertTrue(manuscript_budget_is_missing(draft, None))
        research = ProposalResearchCache(
            rfpId="rfp-regen", updatedAt="2026-08-05T00:00:00+00:00"
        )
        self.assertTrue(manuscript_budget_is_missing(draft, research))

    def test_present_when_budget_model_exists(self) -> None:
        budget = ProposalBudget(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Strategy",
                    category="Labor",
                    extended=5000,
                )
            ],
            lumpSumTotal=5000,
            agencyRevenueEstimate=5000,
        )
        research = ProposalResearchCache(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        draft = ProposalDraft(
            rfpId="rfp-regen",
            sections=[],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        self.assertFalse(manuscript_budget_is_missing(draft, research))

    def test_travel_only_cost_proposal_counts_as_missing(self) -> None:
        hollow = (
            "Proposed Investment\n"
            "Direct travel / reimbursables: $2,500\n"
            "Total proposed investment: $2,500 Rates follow zö's Industry Average "
            "pricing guide for comparable municipal / education marketing engagements.\n\n"
            "Complete website redesign. Total proposed investment: $2,500 "
            "($2,500 in direct travel expenses).\n"
        )
        self.assertTrue(manuscript_cost_section_is_hollow(hollow))
        travel_budget = ProposalBudget(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="T1",
                    description="Travel / reimbursables",
                    category="Travel",
                    extended=2500,
                )
            ],
            lumpSumTotal=2500,
            directExpensesTotal=2500,
            agencyRevenueEstimate=0,
        )
        self.assertTrue(pricing_model_lacks_professional_fees(travel_budget))
        draft = ProposalDraft(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="sec-cost",
                    title=(
                        "Cost Proposal That Includes All Charges, Including "
                        "One-Time Build and Migration and One-Time Recommended On-Site Training"
                    ),
                    content=hollow,
                    status="generated",
                )
            ],
        )
        research = ProposalResearchCache(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=travel_budget,
        )
        self.assertTrue(manuscript_budget_is_missing(draft, research))


class BudgetRegenWiringTests(unittest.TestCase):
    def test_missing_budget_calls_phase_3_5(self) -> None:
        rfp_id = "rfp-regen"
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="sec-1",
                    title="Approach",
                    content="We will deliver the work.",
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId=rfp_id, updatedAt="2026-08-05T00:00:00+00:00"
        )
        regenerated_budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Strategy",
                    category="Labor",
                    extended=5000,
                )
            ],
            lumpSumTotal=5000,
            agencyRevenueEstimate=5000,
        )
        regenerated_research = research.model_copy(update={"budget": regenerated_budget})
        regenerated_draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content="| Line | Cost |\n|---|---|\n| Strategy | $5,000 |",
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )

        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "_regen_budget_via_phase_3_5",
            new=AsyncMock(
                return_value=(regenerated_draft, regenerated_research, regenerated_budget)
            ),
        ) as regen, patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, out_research, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id=rfp_id,
                    rfp=_rfp(rfp_id),
                    draft=draft,
                    research=research,
                    rfp_text="Cost proposal required. Submit itemized budget.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        regen.assert_awaited_once_with(rfp_id)
        self.assertTrue(meta["budgetRegenerated"])
        self.assertEqual(meta["budgetStatus"], "repaired")
        self.assertIsNotNone(out_research and out_research.budget)
        self.assertTrue(any("regenerated via Phase 3.5" in line for line in logs))
        self.assertTrue(
            any("Budget" in (s.title or "") for s in out_draft.sections)
        )


if __name__ == "__main__":
    unittest.main()
