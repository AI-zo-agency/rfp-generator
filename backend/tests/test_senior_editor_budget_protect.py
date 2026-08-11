"""Senior Editor must never delete the Budget / Pricing tab."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_budget_content import ensure_budget_section_present
from app.services.proposal_self_edit_loop import SelfEditReport, _apply_senior_editor_tickets


def _draft_with_budget() -> ProposalDraft:
    budget_body = (
        "## Proposed Investment\n\nTotal $100,000\n\n"
        "| Phase | Amount |\n| --- | ---: |\n| Media | $100,000 |\n"
    )
    return ProposalDraft(
        rfp_id="r1",
        updated_at="t",
        sections=[
            ProposalSection(
                id="section-budget-pricing",
                title="Budget & Pricing",
                content=budget_body,
                status="generated",
            ),
            ProposalSection(
                id="rfp-twin",
                title="Fee Narrative Clone",
                content=budget_body + "\nExtra fee prose.\n",
                status="generated",
            ),
            ProposalSection(
                id="rfp-scope",
                title="Understanding of Scope",
                content=("Audience and channel strategy. " * 30),
                status="generated",
            ),
        ],
    )


class SeniorEditorBudgetProtectTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_tickets_cannot_drop_budget_tab(self) -> None:
        draft = _draft_with_budget()
        report = SelfEditReport(iterations_run=1)
        tickets = {
            "deleteSectionTickets": [
                {
                    "sectionId": "section-budget-pricing",
                    "keepSectionId": "rfp-twin",
                    "reason": "duplicate fees",
                },
                {
                    "sectionId": "rfp-twin",
                    "keepSectionId": "rfp-scope",
                    "reason": "overlap",
                },
            ],
            "dedupeTickets": [],
            "coverageTickets": [],
            "complianceTickets": [],
        }
        with patch(
            "app.services.proposal_self_edit_loop.asave_proposal_draft",
            new_callable=AsyncMock,
        ):
            out, _ = await _apply_senior_editor_tickets(
                tickets=tickets,
                rfp_id="r1",
                rfp=MagicMock(),
                draft=draft,
                research=None,
                report=report,
            )
        ids = {s.id for s in out.sections}
        self.assertIn("section-budget-pricing", ids)
        self.assertNotIn("rfp-twin", ids)
        self.assertTrue(
            any("Blocked" in str(row.get("detail") or "") for row in report.section_logs)
        )

    def test_ensure_budget_restores_when_missing(self) -> None:
        from app.models.proposal import BudgetLineItem

        sections = [
            ProposalSection(
                id="rfp-scope",
                title="Scope",
                content="Work plan.",
                status="generated",
            )
        ]
        budget = ProposalBudget(
            rfp_id="r1",
            pricing_tiers="Average",
            updated_at="t",
            lump_sum_total=50000,
            line_items=[
                BudgetLineItem(
                    id="L1",
                    category="Creative",
                    description="Campaign creative",
                    quantity=1,
                    unit="project",
                    rate=50000,
                    extended=50000,
                )
            ],
        )
        restored, did = ensure_budget_section_present(sections, budget)
        self.assertTrue(did)
        self.assertTrue(any(s.id == "section-budget-pricing" for s in restored))
        self.assertIn("$", restored[-1].content or "")


if __name__ == "__main__":
    unittest.main()
