"""Senior editor mechanical section coverage audit."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection, ProposalResearchCache
from app.models.proposal import RfpSectionMap
from app.services.proposal_senior_editor_coverage import (
    apply_senior_editor_section_coverage_audit,
)


class SeniorEditorCoverageAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_pointer_only_tab_emits_coverage_ticket(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="rfp-bg",
                    title="Background and Experience",
                    content=(
                        "The company background for this submission is Sections 1.1–1.5 below "
                        "(Who We Are through Insurance Information)."
                    ),
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        research = ProposalResearchCache(
            rfpId="rfp-x",
            rfpSections=[
                RfpSectionMap(
                    id="rfp-bg",
                    title="Background and Experience",
                    required=True,
                    evaluationWeight=15,
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        with patch(
            "app.services.proposal_senior_editor_coverage.extract_rfp_scored_section_specs",
            new=AsyncMock(return_value=[]),
        ):
            updated, logs, tickets = await apply_senior_editor_section_coverage_audit(
                draft,
                research=research,
                rfp_text="Proposal shall include Background and Experience.",
                rfp_title="Test RFP",
            )
        self.assertTrue(any("pointer-only" in x.casefold() for x in logs))
        self.assertTrue(
            any(t.get("sectionId") == "rfp-bg" for t in tickets if isinstance(t, dict))
        )
        self.assertIn("MANUAL FILL", updated.sections[0].content or "")

    async def test_missing_outline_tab_gets_stub_and_coverage_ticket(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="rfp-cover",
                    title="Cover Letter",
                    content="Dear evaluators, we are pleased to submit.",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        research = ProposalResearchCache(
            rfpId="rfp-x",
            rfpSections=[
                RfpSectionMap(
                    id="rfp-tech",
                    title="Technical Approach",
                    required=True,
                    evaluationWeight=30,
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        with patch(
            "app.services.proposal_senior_editor_coverage.extract_rfp_scored_section_specs",
            new=AsyncMock(return_value=[]),
        ):
            updated, _logs, tickets = await apply_senior_editor_section_coverage_audit(
                draft,
                research=research,
                rfp_text="Include Technical Approach.",
                rfp_title="Test RFP",
            )
        titles = [s.title for s in updated.sections]
        self.assertTrue(any("Technical Approach" in (t or "") for t in titles))
        self.assertTrue(
            any(
                "too thin" in str(t.get("unmetRequirements")).casefold()
                or "Missing RFP tab" in str(t.get("unmetRequirements"))
                or "stub" in str(t.get("rewriteBrief", "")).casefold()
                for t in tickets
                if isinstance(t, dict)
            )
        )


if __name__ == "__main__":
    unittest.main()
