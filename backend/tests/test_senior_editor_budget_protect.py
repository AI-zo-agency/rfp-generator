"""Senior Editor must never delete TOC tabs (budget, RFP twins, or otherwise)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_budget_content import ensure_budget_section_present
from app.services.proposal_self_edit_loop import (
    SelfEditReport,
    _apply_senior_editor_tickets,
    _manuscript_digest_for_senior_editor,
    normalize_senior_editor_tickets,
)


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
    async def test_delete_tickets_cannot_drop_any_toc_tab(self) -> None:
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
        with (
            patch(
                "app.services.proposal_self_edit_loop.asave_proposal_draft",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.proposal_self_edit_loop.aget_proposal_draft",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.proposal_self_edit_loop.aget_research_cache",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.proposal_self_edit_loop._repair_one_section",
                new_callable=AsyncMock,
                return_value=("rfp-twin", False, "trim skipped"),
            ),
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
        self.assertIn("rfp-twin", ids)
        self.assertIn("rfp-scope", ids)
        self.assertTrue(
            any(
                "Refused deleteSectionTickets" in str(row.get("detail") or "")
                for row in report.section_logs
            )
        )
        self.assertFalse(
            any("Deleted" in str(row.get("detail") or "") for row in report.section_logs)
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


class SeniorEditorTicketNormalizeTests(unittest.TestCase):
    def test_deletes_become_trim_dedupe_tickets(self) -> None:
        out = normalize_senior_editor_tickets(
            {
                "deleteSectionTickets": [
                    {
                        "sectionId": "rfp-capacity",
                        "keepSectionId": "rfp-staffing",
                        "reason": "same headcount table",
                    }
                ],
                "dedupeTickets": [
                    {
                        "sectionId": "rfp-approach",
                        "keepHomeSectionId": "section-1-who",
                        "trimGuidance": "drop who-we-are dump",
                    }
                ],
                "coverageTickets": [],
                "complianceTickets": [],
                "notes": [],
            }
        )
        self.assertEqual(out["deleteSectionTickets"], [])
        ids = [t["sectionId"] for t in out["dedupeTickets"]]
        self.assertEqual(ids[0], "rfp-capacity")
        self.assertIn("KEEP this tab", out["dedupeTickets"][0]["trimGuidance"])
        self.assertEqual(ids[1], "rfp-approach")
        self.assertTrue(
            any("rfp-capacity" in n for n in out["notes"]),
        )

    def test_digest_leads_with_full_toc(self) -> None:
        digest = _manuscript_digest_for_senior_editor(_draft_with_budget())
        self.assertIn("FULL TABLE OF CONTENTS", digest)
        self.assertIn("`section-budget-pricing`", digest)
        self.assertIn("`rfp-twin`", digest)
        self.assertIn("`rfp-scope`", digest)
        self.assertLess(digest.index("FULL TABLE OF CONTENTS"), digest.index("## Section excerpts"))


if __name__ == "__main__":
    unittest.main()
