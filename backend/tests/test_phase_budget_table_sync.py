"""Canonical budget must overwrite invented phase tables in sibling sections."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_budget_content import sync_phase_budget_tables_across_draft


def _budget() -> ProposalBudget:
    def _line(item_id: str, category: str, description: str, extended: float) -> BudgetLineItem:
        return BudgetLineItem(
            id=item_id,
            category=category,
            description=description,
            extended=extended,
            lineItemType="agency_fee",
        )

    return ProposalBudget(
        rfpId="rfp-test",
        updatedAt="2026-01-01T00:00:00Z",
        lineItems=[
            _line("L1", "Discovery", "Stakeholder interviews", 6500.0),
            _line("L2", "Brand Development", "Identity system", 7500.0),
            _line("L3", "Website UX", "Wireframes", 9000.0),
            _line("L4", "Development & CMS", "WordPress build", 16500.0),
            _line("L5", "QA", "Testing", 4000.0),
            _line("L6", "Training", "Staff training", 3500.0),
            _line("L7", "Launch", "Go-live", 3000.0),
        ],
        totalClientInvoicing=50000.0,
    )


class PhaseBudgetTableSyncTests(unittest.TestCase):
    def test_sync_overwrites_contradictory_disbursement_and_allocation_tabs(self) -> None:
        invented_disbursement = (
            "### Disbursement Schedule\n\n"
            "| Phase | Amount |\n"
            "| --- | ---: |\n"
            "| Discovery | $8,000 |\n"
            "| Strategy & Brand | $12,000 |\n"
            "| Design & Dev | $15,000 |\n"
            "| Dev & Migration | $10,000 |\n"
            "| Launch & Training | $5,000 |\n"
            "| **Total** | **$50,000** |\n"
        )
        invented_allocation = (
            "### Budget Allocation\n\n"
            "| Phase | Amount |\n"
            "| --- | ---: |\n"
            "| Discovery | $8,000 |\n"
            "| Brand Dev | $9,000 |\n"
            "| UX | $10,000 |\n"
            "| Dev & CMS | $15,000 |\n"
            "| QA | $3,500 |\n"
            "| Training | $2,500 |\n"
            "| Launch | $2,000 |\n"
            "| **Total** | **$50,000** |\n"
        )
        draft = ProposalDraft(
            rfpId="rfp-test",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content="### Proposed Investment\n\nCanonical tab.\n",
                ),
                ProposalSection(
                    id="disbursement",
                    title="Disbursement Schedule",
                    content=invented_disbursement,
                ),
                ProposalSection(
                    id="allocation",
                    title="Budget Allocation",
                    content=invented_allocation,
                ),
            ],
        )
        budget = _budget()

        updated, logs = sync_phase_budget_tables_across_draft(draft, budget)

        self.assertEqual(len(logs), 2)
        disbursement = updated.sections[1].content or ""
        allocation = updated.sections[2].content or ""
        self.assertIn("$6,500", disbursement)
        self.assertIn("Discovery", disbursement)
        self.assertNotIn("$8,000", disbursement)
        self.assertNotIn("Strategy & Brand", disbursement)
        self.assertIn("$6,500", allocation)
        self.assertIn("$7,500", allocation)
        self.assertNotIn("Brand Dev | $9,000", allocation)

    def test_sync_strips_phase_table_from_approach_with_cross_ref(self) -> None:
        approach = (
            "## Approach\n\n"
            "We work in phases.\n\n"
            "### Fee Detail by Phase\n\n"
            "| Phase | Amount |\n"
            "| --- | ---: |\n"
            "| Discovery | $8,000 |\n"
            "| Launch | $42,000 |\n"
        )
        draft = ProposalDraft(
            rfpId="rfp-test",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content="### Proposed Investment\n\n",
                ),
                ProposalSection(
                    id="approach",
                    title="Approach & Methodology",
                    content=approach,
                ),
            ],
        )
        updated, logs = sync_phase_budget_tables_across_draft(draft, _budget())
        self.assertEqual(len(logs), 1)
        body = updated.sections[1].content or ""
        self.assertNotIn("$8,000", body)
        self.assertIn("Budget & Pricing", body)
        self.assertIn("We work in phases", body)


if __name__ == "__main__":
    unittest.main()
