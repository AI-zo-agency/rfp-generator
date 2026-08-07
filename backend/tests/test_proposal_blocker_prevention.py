"""Tests for shared feedback-blocker prevention suite."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_blocker_prevention import (
    clean_case_study_label,
    scrub_case_study_section_titles,
)


class CaseStudyLabelTests(unittest.TestCase):
    def test_strips_won_filename_and_compressed(self) -> None:
        out = clean_case_study_label(
            "06_WON_CityofCarbondale_Proposal_2025_compressed.pdf",
            index=1,
        )
        self.assertEqual(out, "3.1 — City of Carbondale 2025")
        self.assertNotIn("WON", out)
        self.assertNotIn("compressed", out.casefold())
        self.assertNotIn(".pdf", out.casefold())

    def test_strips_03_cs_prefix(self) -> None:
        out = clean_case_study_label("03_CS_OregonEmployment.pdf", index=2)
        self.assertTrue(out.startswith("3.2 —"))
        self.assertIn("Oregon", out)
        self.assertNotIn("03_CS", out)


class ScrubTitlesTests(unittest.TestCase):
    def test_renames_filename_section_titles(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="section-3-work-01-06_won_cityofcarbondale_proposal_2025_co",
                    title="3.1 — 06_WON_CityofCarbondale_Proposal_2025_compressed",
                    content="Challenge: …",
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-sec-1",
                    title="Cover Letter",
                    content="Dear Committee,",
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        out, logs = scrub_case_study_section_titles(draft)
        self.assertTrue(logs)
        cs = out.sections[0]
        self.assertNotIn("06_WON", cs.title or "")
        self.assertNotIn("compressed", (cs.title or "").casefold())
        self.assertEqual(out.sections[1].title, "Cover Letter")


if __name__ == "__main__":
    unittest.main()
