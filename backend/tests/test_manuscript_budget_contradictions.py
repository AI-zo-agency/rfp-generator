"""Tests for cross-section budget / hours / fee contradiction scan."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_manuscript_budget_contradictions import (
    _parse_findings,
    run_manuscript_budget_contradiction_pass,
)


def _rfp(**overrides) -> RfpRecord:
    base = {
        "id": "rfp-x",
        "title": "DuPage County",
        "client": "DuPage County",
        "sector": "public",
        "dueDate": "2026-08-21",
        "receivedDate": "2026-08-01",
        "status": "active",
        "lastActivity": "2026-08-05",
        "lastActivityNote": "n",
    }
    base.update(overrides)
    return RfpRecord.model_validate(base)


class BudgetContradictionParseTests(unittest.TestCase):
    def test_parse_double_billed_pm(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-budget",
                    title="Budget & Pricing",
                    content="Planning & Account Management $7,500\nProject Management $7,500",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-12T00:00:00Z",
        )
        raw = {
            "contradictions": [
                {
                    "sectionId": "section-budget",
                    "sectionTitle": "Budget & Pricing",
                    "canonicalFact": "Both lines claim planning meetings and status reporting",
                    "manuscriptContradiction": "Planning PM and Project Management overlap ($15k total)",
                    "severity": "major",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Merge into one coordination line at $7,500",
                }
            ]
        }
        findings = _parse_findings(raw, draft)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].fix_action, "rewrite")


class BudgetContradictionPassTests(unittest.IsolatedAsyncioTestCase):
    async def test_flags_and_rewrites(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-budget",
                    title="Budget & Pricing",
                    content="| Planning & Account Management | $7,500 |\n| Project Management | $7,500 |",
                    status="generated",
                ),
                ProposalSection(
                    id="section-capacity",
                    title="Monthly Capacity Allocation",
                    content="Total: 235 hours/month",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-12T00:00:00Z",
        )

        audit_response = {
            "contradictions": [
                {
                    "sectionId": "section-budget",
                    "manuscriptContradiction": "Double-billed PM/planning coordination",
                    "canonicalFact": "Both claim status reporting",
                    "severity": "major",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Keep one PM line only",
                }
            ],
            "summary": "Merged duplicate coordination lines",
        }
        rewrite_response = {
            "content": "| Project Management (planning + coordination) | $7,500 |",
            "changed": True,
            "notes": "merged",
        }

        async def fake_chat(messages, **kwargs):
            node = kwargs.get("node_name") or ""
            if "audit" in node:
                return audit_response, "stub"
            return rewrite_response, "stub"

        with (
            patch(
                "app.services.proposal_manuscript_budget_contradictions.llm.is_configured",
                return_value=True,
            ),
            patch(
                "app.services.proposal_manuscript_budget_contradictions.llm.chat_json",
                side_effect=fake_chat,
            ),
            patch(
                "app.services.proposal_pricing_service.fetch_pricing_guide_context",
                new_callable=AsyncMock,
                return_value=("PM floor $7,500", []),
            ),
        ):
            result = await run_manuscript_budget_contradiction_pass(
                draft, rfp=_rfp(), research=None, use_llm=True
            )

        self.assertEqual(result.rewrites_applied, 1)
        budget = next(s for s in result.draft.sections if s.id == "section-budget")
        self.assertIn("$7,500", budget.content or "")


if __name__ == "__main__":
    unittest.main()
