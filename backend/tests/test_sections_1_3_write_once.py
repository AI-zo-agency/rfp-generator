"""Sections 1–3 write-once: partial saves must not trigger per-subsection fact-check."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_generator import (
    section_1_subsections_complete,
    section_2_track_complete,
    section_3_track_complete,
)


def _section(sid: str, content: str = "Body text.") -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=sid,
        content=content,
        status="generated",
    )


class SectionTrackCompletionTests(unittest.TestCase):
    def test_section_1_incomplete_without_who_we_are(self) -> None:
        sections = [
            _section("section-1-org-structure"),
            _section("section-1-business-info"),
            _section("section-1-certifications"),
            _section("section-1-insurance"),
        ]
        self.assertFalse(section_1_subsections_complete(sections))

    def test_section_1_complete_with_all_required(self) -> None:
        sections = [
            _section("section-1-who-we-are"),
            _section("section-1-org-structure"),
            _section("section-1-business-info"),
            _section("section-1-certifications"),
            _section("section-1-insurance"),
        ]
        self.assertTrue(section_1_subsections_complete(sections))

    def test_section_2_complete_with_bio(self) -> None:
        sections = [_section("section-2-bio-sonja-hoel", "Bio body.")]
        self.assertTrue(section_2_track_complete(sections))

    def test_section_3_complete_with_work_card(self) -> None:
        sections = [
            ProposalSection(
                id="section-3-work-waterwise",
                title="City of Bend WaterWise",
                content="Challenge: drought outreach. Solution: brand toolkit.",
                status="generated",
            )
        ]
        self.assertTrue(section_3_track_complete(sections))


class PartialPersistFactCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_persist_does_not_fact_check(self) -> None:
        from app.services.proposal_generator import _persist_sections_1_3_partial

        partial = [
            ProposalSection(
                id="section-1-who-we-are",
                title="1.1 — Who We Are",
                content="We are zö agency.",
                status="generated",
            )
        ]
        with (
            patch(
                "app.services.proposal_generator.aget_proposal_draft",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.proposal_generator.asave_proposal_draft",
                new=AsyncMock(),
            ),
            patch(
                "app.services.proposal_generator.get_rfp",
                return_value=type("R", (), {"page_limit": 30})(),
            ),
            patch(
                "app.services.proposal_generator._incremental_fact_check_after_sections",
                new=AsyncMock(),
            ) as mock_fc,
        ):
            await _persist_sections_1_3_partial("rfp-x", partial, "test-provider")
        mock_fc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
