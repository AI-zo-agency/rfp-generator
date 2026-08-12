"""Tests for LLM manuscript internal + KB fact-contradiction scan."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_manuscript_fact_contradictions import (
    _parse_findings,
    run_manuscript_fact_contradiction_pass,
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


class FactContradictionParseTests(unittest.TestCase):
    def test_parse_team_size_finding(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="1.1 — Who We Are",
                    content="Our core team of 20 full-time professionals...",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-12T00:00:00Z",
        )
        raw = {
            "contradictions": [
                {
                    "sectionId": "section-1-who-we-are",
                    "sectionTitle": "1.1 — Who We Are",
                    "verifiedFact": "01_companyfacts_verified: Team Size: 35",
                    "manuscriptContradiction": (
                        "Claims 20 full-time professionals plus 35+ specialists "
                        "— invented split not in companyfacts"
                    ),
                    "severity": "critical",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "State team size as 35 per companyfacts only.",
                }
            ],
            "summary": "Team size fabrication in Who We Are.",
        }
        findings = _parse_findings(raw, draft)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].section_id, "section-1-who-we-are")
        self.assertIn("35", findings[0].verified_fact)


class FactContradictionPassTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewrite_applied_for_critical_team_size(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="1.1 — Who We Are",
                    content=(
                        "Our core team of 20 full-time professionals is supported by "
                        "a network of specialized contractors, giving us access to "
                        "35+ specialists across disciplines."
                    ),
                    status="generated",
                ),
            ],
            updatedAt="2026-08-12T00:00:00Z",
        )
        audit_json = {
            "contradictions": [
                {
                    "sectionId": "section-1-who-we-are",
                    "verifiedFact": "Team Size: 35 (01_companyfacts_verified)",
                    "manuscriptContradiction": "20 full-time + 35+ specialists split",
                    "severity": "critical",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Use Team Size 35 only.",
                }
            ],
            "summary": "Invented team size split.",
        }
        rewrite_json = {
            "content": (
                "We are zö agency — a team of 35 professionals based in Bend, Oregon. "
                "We bring thirteen years of lived experience serving public-sector "
                "and mission-driven clients with warmth, transparency, and proof-led work.\n\n"
                "## Our Promise\n"
                "Excellence is our guarantee, not our goal. We meet deadlines, "
                "stay within budget, and give clients direct access to the people "
                "doing the work — no surprise bills, no black boxes."
            ),
            "changed": True,
            "notes": "Aligned team size with companyfacts.",
        }

        async def _fake_corpus(*_a, **_k):
            return "01_companyfacts verified.docx\nTeam Size: 35\n", ["01_companyfacts verified.docx"]

        with patch(
            "app.services.proposal_manuscript_fact_contradictions._fetch_verified_facts_corpus",
            new=AsyncMock(side_effect=_fake_corpus),
        ), patch(
            "app.services.proposal_manuscript_fact_contradictions.llm.chat_json",
            new=AsyncMock(side_effect=[(audit_json, "test"), (rewrite_json, "test")]),
        ), patch(
            "app.services.proposal_manuscript_fact_contradictions.llm.is_configured",
            return_value=True,
        ):
            result = await run_manuscript_fact_contradiction_pass(
                draft,
                rfp=_rfp(),
                use_llm=True,
            )

        self.assertEqual(result.rewrites_applied, 1)
        self.assertNotIn("20 full-time", result.draft.sections[0].content or "")
        self.assertIn("35", result.draft.sections[0].content or "")


if __name__ == "__main__":
    unittest.main()
