"""Tests for LLM RFP contradiction scan helpers + DQ banner filtering."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_scan_dq_orchestrator import (
    collect_go_no_go_dq_risks,
    collect_go_no_go_review_gaps,
)
from app.services.proposal_scan_rfp_contradictions import (
    _parse_findings,
    run_scan_rfp_contradiction_pass,
)


def _rfp(**overrides) -> RfpRecord:
    base = {
        "id": "rfp-x",
        "title": "Talent RFP",
        "client": "City",
        "sector": "public",
        "dueDate": "2026-08-21",
        "receivedDate": "2026-08-01",
        "status": "active",
        "lastActivity": "2026-08-05",
        "lastActivityNote": "n",
    }
    base.update(overrides)
    return RfpRecord.model_validate(base)


class GoNoGoDqFilterTests(unittest.TestCase):
    def test_capability_gaps_not_in_dq_banner(self) -> None:
        rfp = _rfp(
            goNoGo="review",
            goNoGoAnalysis={
                "recommendation": "review",
                "criticalGaps": [
                    "No documented talent attraction or workforce recruitment campaign experience",
                    "Unverified capability claim — Creative asset development: Brittany Frazier",
                ],
                "scopeMatch": {
                    "flags": [
                        {
                            "severity": "critical",
                            "category": "capability",
                            "message": "Unverified capability claim — Research services",
                        }
                    ]
                },
                "compliance": {
                    "flags": [
                        {
                            "severity": "critical",
                            "category": "eligibility",
                            "message": "Must be registered to do business in Wisconsin",
                        }
                    ]
                },
            },
        )
        dq = collect_go_no_go_dq_risks(rfp)
        review = collect_go_no_go_review_gaps(rfp)
        self.assertTrue(any("registered" in r.casefold() for r in dq))
        self.assertFalse(any("talent attraction" in r.casefold() for r in dq))
        self.assertFalse(any("brittany" in r.casefold() for r in dq))
        self.assertTrue(any("talent attraction" in g.casefold() for g in review))


class ContradictionParseTests(unittest.TestCase):
    def test_parse_findings_maps_section_ids(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="rfp-sec-7",
                    title="Project Schedule",
                    content="Phase 5 Week 10 handoff.",
                    status="generated",
                )
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        raw = {
            "contradictions": [
                {
                    "sectionId": "rfp-sec-7",
                    "sectionTitle": "Project Schedule",
                    "rfpRequirement": "Launch within 4 weeks of award",
                    "manuscriptContradiction": "Invented 10-week sequential plan",
                    "severity": "critical",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Replace with VERIFY calendar within window",
                }
            ]
        }
        findings = _parse_findings(raw, draft)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].section_id, "rfp-sec-7")
        self.assertEqual(findings[0].fix_action, "rewrite")


class ContradictionPassTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_rewrite_then_falls_back_to_verify(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="sched",
                    title="Project Schedule",
                    content="We deliver a full 10-week sequential plan ending Week 10. " * 20,
                    status="generated",
                )
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        rfp = _rfp()
        audit = {
            "contradictions": [
                {
                    "sectionId": "sched",
                    "sectionTitle": "Project Schedule",
                    "rfpRequirement": "Launch within 4 weeks of award",
                    "manuscriptContradiction": "10-week plan overruns window",
                    "severity": "major",
                    "fixAction": "verify",
                    "rewriteInstruction": "Compress calendar",
                }
            ],
            "attachmentNeeds": [],
            "complianceReminders": ["PDF due August 21"],
            "summary": "Schedule overruns RFP window",
        }
        # Rewrite refused (too thin) → VERIFY fallback
        rewrite_fail = {"content": "short", "changed": True, "notes": "thin"}
        mock = AsyncMock(side_effect=[(audit, "test"), (rewrite_fail, "test")])
        with patch(
            "app.services.proposal_scan_rfp_contradictions.llm.chat_json",
            new=mock,
        ), patch(
            "app.services.proposal_scan_rfp_contradictions.llm.is_configured",
            return_value=True,
        ):
            result = await run_scan_rfp_contradiction_pass(
                draft,
                rfp=rfp,
                rfp_text="Award then launch within 4 weeks. " * 20,
                use_llm=True,
            )
        self.assertEqual(result.verify_tags_added, 1)
        self.assertEqual(len(result.unresolved_findings), 1)
        self.assertIn("[VERIFY: resolve RFP contradiction", result.draft.sections[0].content or "")

    async def test_rewrite_clears_unresolved(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="sched",
                    title="Project Schedule",
                    content="We deliver a full 10-week sequential plan ending Week 10. " * 20,
                    status="generated",
                )
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        rfp = _rfp()
        audit = {
            "contradictions": [
                {
                    "sectionId": "sched",
                    "sectionTitle": "Project Schedule",
                    "rfpRequirement": "Launch within 4 weeks of award",
                    "manuscriptContradiction": "10-week plan overruns window",
                    "severity": "critical",
                    "fixAction": "rewrite",
                    "rewriteInstruction": "Compress",
                }
            ],
            "summary": "ok",
        }
        fixed_body = (
            "## Project Schedule\n\n"
            "Delivery calendar within RFP award→launch window.\n\n"
            "| Phase | Timing | Milestone |\n"
            "| --- | --- | --- |\n"
            "| Discovery | Week 1 | Kickoff |\n"
            "| Launch | Week 3-4 | Live |\n\n"
            "Parallel workstreams keep launch inside the RFP window."
        )
        rewrite_ok = {"content": fixed_body, "changed": True, "notes": "compressed"}
        mock = AsyncMock(side_effect=[(audit, "t"), (rewrite_ok, "t")])
        with patch(
            "app.services.proposal_scan_rfp_contradictions.llm.chat_json",
            new=mock,
        ), patch(
            "app.services.proposal_scan_rfp_contradictions.llm.is_configured",
            return_value=True,
        ):
            result = await run_scan_rfp_contradiction_pass(
                draft,
                rfp=rfp,
                rfp_text="Award then launch within 4 weeks. " * 20,
                use_llm=True,
            )
        self.assertEqual(result.rewrites_applied, 1)
        self.assertEqual(result.unresolved_findings, [])
        self.assertIn("award→launch", result.draft.sections[0].content or "")


if __name__ == "__main__":
    unittest.main()
