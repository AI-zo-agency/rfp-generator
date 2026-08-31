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
        # Patch model: replace only the fabricated team-size span, verbatim.
        rewrite_json = {
            "edits": [
                {
                    "find": (
                        "Our core team of 20 full-time professionals is supported by "
                        "a network of specialized contractors, giving us access to "
                        "35+ specialists across disciplines."
                    ),
                    "replace": "Our core team of 35 full-time professionals works across disciplines.",
                }
            ],
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

    async def test_skips_rewrite_on_section_2_bio_stub(self) -> None:
        from app.services.proposal_bio_stub import format_bio_stub_content

        stub = format_bio_stub_content(
            member="Ella Lindau",
            role="Account and Operations Manager",
        )
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-2-bio-ella-lindau",
                    title="2.3 — Ella Lindau",
                    content=stub,
                    status="generated",
                ),
            ],
            updatedAt="2026-08-17T00:00:00Z",
        )
        audit_json = {
            "contradictions": [
                {
                    "sectionId": "section-2-bio-ella-lindau",
                    "verifiedFact": "Org chart lists Project Manager",
                    "manuscriptContradiction": "Role says Account and Operations Manager",
                    "severity": "critical",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Rewrite the full bio from 04_Bio.",
                }
            ],
            "summary": "Role mismatch.",
        }
        rewrite_json = {
            "content": (
                "### Ella Lindau\nACCOUNT AND OPERATIONS MANAGER | 5 YEARS WITH ZÖ "
                "AGENCY\nElla has 5 years of healthcare and government experience."
            ),
            "changed": True,
            "notes": "Expanded from 04_Bio.",
        }

        async def _fake_corpus(*_a, **_k):
            return "Team Size: 35\n", ["01_companyfacts"]

        chat = AsyncMock(side_effect=[(audit_json, "test"), (rewrite_json, "test")])
        with patch(
            "app.services.proposal_manuscript_fact_contradictions._fetch_verified_facts_corpus",
            new=AsyncMock(side_effect=_fake_corpus),
        ), patch(
            "app.services.proposal_manuscript_fact_contradictions.llm.chat_json",
            new=chat,
        ), patch(
            "app.services.proposal_manuscript_fact_contradictions.llm.is_configured",
            return_value=True,
        ):
            result = await run_manuscript_fact_contradiction_pass(
                draft,
                rfp=_rfp(),
                use_llm=True,
            )

        self.assertEqual(result.rewrites_applied, 0)
        self.assertEqual(result.draft.sections[0].content, stub)
        self.assertEqual(chat.await_count, 1)
        self.assertTrue(
            any(
                "skipped fact-contradiction rewrite on bio PDF designer-note stub" in line
                for line in result.logs
            )
        )

    async def test_rewrite_applied_on_bio_with_real_narrative_content(self) -> None:
        """A bio with actual narrative prose (not a PDF designer-note stub) must
        get the same fact-consistency treatment as any other section — the
        blanket bio skip exists only to protect stubs from being clobbered."""
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-2-bio-sonja-anderson",
                    title="2.1 — Sonja Anderson",
                    content=(
                        "### Sonja Anderson\n"
                        "**Role on this engagement:** Agency Director\n\n"
                        "Sonja brings 25 years of marketing industry experience and "
                        "three decades of finding the growth pathways others miss."
                    ),
                    status="generated",
                ),
            ],
            updatedAt="2026-08-12T00:00:00Z",
        )
        audit_json = {
            "contradictions": [
                {
                    "sectionId": "section-2-bio-sonja-anderson",
                    "verifiedFact": "01_companyfacts_verified: 25 years marketing experience",
                    "manuscriptContradiction": (
                        "Bio claims both '25 years' and 'three decades' for the same tenure"
                    ),
                    "severity": "major",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Keep 25 years only; remove 'three decades'.",
                }
            ],
            "summary": "Internal numeric self-contradiction in bio.",
        }
        rewrite_json = {
            "edits": [
                {
                    "find": (
                        "Sonja brings 25 years of marketing industry experience and "
                        "three decades of finding the growth pathways others miss."
                    ),
                    "replace": (
                        "Sonja brings 25 years of marketing industry experience "
                        "finding the growth pathways others miss."
                    ),
                }
            ],
            "changed": True,
            "notes": "Removed contradicting decades claim.",
        }

        async def _fake_corpus(*_a, **_k):
            return "01_companyfacts verified.docx\n25 years marketing experience\n", [
                "01_companyfacts verified.docx"
            ]

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
        self.assertNotIn("three decades", result.draft.sections[0].content or "")
        self.assertIn("25 years", result.draft.sections[0].content or "")
        self.assertFalse(
            any("skipped fact-contradiction rewrite" in line for line in result.logs)
        )

    async def test_only_rewrite_section_ids_skips_other_tabs(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="1.1 — Who We Are",
                    content="Our core team of 20 full-time professionals.",
                    status="generated",
                ),
                ProposalSection(
                    id="section-1-business-info",
                    title="1.3 — Business Information",
                    content="Email: info@zo.agency",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-17T00:00:00Z",
        )
        audit_json = {
            "contradictions": [
                {
                    "sectionId": "section-1-business-info",
                    "verifiedFact": "Email: connect@zo.agency",
                    "manuscriptContradiction": "Wrong email in business info",
                    "severity": "critical",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Use connect@zo.agency",
                }
            ],
            "summary": "Email mismatch.",
        }
        rewrite_json = {
            "content": "Email: connect@zo.agency",
            "changed": True,
            "notes": "Fixed email.",
        }

        async def _fake_corpus(*_a, **_k):
            return "Email: connect@zo.agency\n", ["01_companyfacts"]

        chat = AsyncMock(side_effect=[(audit_json, "test"), (rewrite_json, "test")])
        with patch(
            "app.services.proposal_manuscript_fact_contradictions._fetch_verified_facts_corpus",
            new=AsyncMock(side_effect=_fake_corpus),
        ), patch(
            "app.services.proposal_manuscript_fact_contradictions.llm.chat_json",
            new=chat,
        ), patch(
            "app.services.proposal_manuscript_fact_contradictions.llm.is_configured",
            return_value=True,
        ):
            result = await run_manuscript_fact_contradiction_pass(
                draft,
                rfp=_rfp(),
                use_llm=True,
                only_rewrite_section_ids=frozenset({"section-1-who-we-are"}),
            )

        self.assertEqual(result.rewrites_applied, 0)
        self.assertEqual(result.draft.sections[1].content, "Email: info@zo.agency")
        self.assertTrue(
            any("outside scoped track" in line for line in result.logs)
        )


class ManuscriptDigestBioCapTests(unittest.TestCase):
    def test_bio_resume_dump_is_truncated_in_digest(self) -> None:
        from app.services.proposal_scan_rfp_contradictions import _manuscript_digest

        dump = "Ella Lindau resume dump. " * 80
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-2-bio-ella-lindau",
                    title="2.3 — Ella Lindau",
                    content=dump,
                    status="generated",
                ),
                ProposalSection(
                    id="form-approach",
                    title="Approach",
                    content="We will run discovery workshops.",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-17T00:00:00Z",
        )
        digest = _manuscript_digest(draft)
        self.assertNotIn("Ella Lindau resume dump. " * 20, digest)
        self.assertIn("form-approach", digest)
        bio_block = digest.split("### id=form-approach")[0]
        self.assertLessEqual(len(bio_block), 600)


if __name__ == "__main__":
    unittest.main()
