"""RFP tabs must not re-draft what static Sections 1-3 already own.

Real symptom: a generated draft carried a section titled "A brief description
of the firm, including the year the firm was established, type of firm
(partnership, corporation, etc.)..." which restated 1.1 Who We Are and
1.3 Business Information. The static-duplicate detector only recognised LABEL
titles ("Company Overview", "Firm Profile"), so an RFP table of contents that
phrases the same ask as a sentence walked straight past it.

The same draft also carried "3.2 - Copy of 03 CS All Case Studies Last
Updated" - a knowledge-base filename that reached the outline as a heading.
"""

from __future__ import annotations

import unittest

from app.services.proposal_outline_dedup import (
    filter_lean_outline_sections,
    is_kb_artefact_outline_title,
)
from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

SENTENCE_FORM = (
    "A brief description of the firm, including the year the firm was "
    "established, type of firm (partnership, corporation, etc.), and a "
    "statement of the firm's qualifications for performing the subject services"
)


class SentenceFormDuplicateTests(unittest.TestCase):
    def test_the_observed_duplicate_is_caught(self) -> None:
        self.assertTrue(is_duplicate_static_rfp_section(SENTENCE_FORM))

    def test_other_sentence_phrasings_are_caught(self) -> None:
        for title in (
            "Provide a brief description of the company",
            "State the year the firm was established",
            "Indicate the type of firm and legal structure",
            "Describe the form of business organization",
            "Number of years in business",
        ):
            self.assertTrue(is_duplicate_static_rfp_section(title), title)

    def test_scored_sections_are_not_swallowed(self) -> None:
        for title in (
            "Scope of Work",
            "Statement of Work",
            "Agency Requirements - Capability Matrix",
            "Sample Work Portfolio",
            "Project Approach and Methodology",
            "Cost Proposal",
            "References",
            "Minimum two recent campaigns",
        ):
            self.assertFalse(is_duplicate_static_rfp_section(title), title)


class KbArtefactTitleTests(unittest.TestCase):
    def test_filenames_are_not_section_titles(self) -> None:
        for title in (
            "3.2 - Copy of 03 CS All Case Studies Last Updated",
            "03_CS_TorrentLaboratories.pdf",
            "04_Bio_ShawnDiCriscio.pdf",
            "06_WON City of Bend",
            "Untitled",
        ):
            self.assertTrue(is_kb_artefact_outline_title(title), title)

    def test_real_titles_survive(self) -> None:
        for title in (
            "Scope of Work",
            "Project Approach",
            "References",
            "Insurance Certificates & Required Attachments",
            "Cost Proposal",
            "RFP SBCOG #2027-02 Response",
            "Section 1 - Company Overview",
        ):
            self.assertFalse(is_kb_artefact_outline_title(title), title)

    def test_artefact_is_dropped_by_the_filter_with_a_reason(self) -> None:
        sections = [
            {"id": "s1", "title": "3.2 - Copy of 03 CS All Case Studies Last Updated",
             "required": True, "order": 1},
            {"id": "s2", "title": "Scope of Work", "required": True, "order": 2},
        ]
        kept, dropped = filter_lean_outline_sections(sections, rfp_context="Scope of Work must be submitted.")

        self.assertEqual([s["title"] for s in kept], ["Scope of Work"])
        self.assertIn("knowledge-base filename", " | ".join(dropped))


if __name__ == "__main__":
    unittest.main()
