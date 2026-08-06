"""RFP tabs must not re-draft what static Sections 1-3 already own.

Real symptom: a generated draft carried a section titled "A brief description
of the firm, including the year the firm was established, type of firm
(partnership, corporation, etc.)..." which restated 1.1 Who We Are and
1.3 Business Information. The static-duplicate detector only recognised LABEL
titles ("Company Overview", "Firm Profile"), so an RFP table of contents that
phrases the same ask as a sentence walked straight past it.

The same draft also carried "3.2 - Copy of 03 CS All Case Studies Last
Updated" - a knowledge-base filename that reached the outline as a heading.

CONDITIONAL SINCE TASK 2 STEP 5. Recognising the sentence form was right; what
was wrong was DELETING the RFP ask without verifying the delegation landed.
Static Sections 1-3 are generated before Phase 2 and never see the RFP, so
"1.3 covers it" was an assumption, not a fact - and when 1.3 happened not to
state the entity type, the ask was dropped and the answer appeared nowhere in
the proposal. These asks are now duplicates only WHEN the static section's own
text demonstrably answers them; otherwise they stay in the outline and the
requirement ledger reports them missing.
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

# Stand-ins for static Section 1.1/1.3 text, each answering a different subset
# of the asks below. Deliberately short: the rule must key on answer-shaped
# CONTENT, not on length (a ">=200 chars of prose" proxy was tried and removed,
# because 200 characters of unrelated prose passed it).
STATIC_ANSWERS_ENTITY_AND_YEAR = (
    "zö agency was established in 2013 and operates as a limited liability "
    "company (LLC) registered in Oregon."
)
STATIC_ANSWERS_ENTITY_ONLY = (
    "zö agency operates as a limited liability company (LLC) registered in Oregon."
)
STATIC_ANSWERS_YEAR_ONLY = (
    "zö agency was established in 2013 and has served public agencies ever since."
)
STATIC_ANSWERS_NEITHER = (
    "We are a full-service creative shop serving public agencies across the region."
)


class SentenceFormDuplicateTests(unittest.TestCase):
    """Each ask is paired with static text that DOES and DOES NOT answer it."""

    def test_the_observed_duplicate_is_caught_when_static_text_answers_it(self) -> None:
        """SENTENCE_FORM enumerates two checkable facts - the founding year AND
        the entity type - so it is a genuine duplicate only when 1.3 states both."""
        self.assertTrue(
            is_duplicate_static_rfp_section(
                SENTENCE_FORM, static_section_text=STATIC_ANSWERS_ENTITY_AND_YEAR
            )
        )

    def test_the_observed_duplicate_is_kept_when_static_text_does_not_answer_it(
        self,
    ) -> None:
        """The defect: dropped on the assumption 1.3 covered it, when it did not."""
        self.assertFalse(
            is_duplicate_static_rfp_section(
                SENTENCE_FORM, static_section_text=STATIC_ANSWERS_NEITHER
            )
        )

    def test_a_compound_ask_is_kept_when_static_text_answers_only_part_of_it(
        self,
    ) -> None:
        """Proving one of the two enumerated facts must not discharge the other -
        a proposal stating the founding year but never the entity type still
        leaves the buyer's question half-answered."""
        for partial in (STATIC_ANSWERS_YEAR_ONLY, STATIC_ANSWERS_ENTITY_ONLY):
            self.assertFalse(
                is_duplicate_static_rfp_section(SENTENCE_FORM, static_section_text=partial),
                partial,
            )

    def test_the_observed_duplicate_is_kept_when_no_static_text_is_available(self) -> None:
        """Outline-authoring time runs before Sections 1-3 exist. With nothing to
        check against, the delegation is unproven - fail closed, keep the ask."""
        self.assertFalse(is_duplicate_static_rfp_section(SENTENCE_FORM))

    def test_entity_type_phrasings_are_caught_only_when_the_entity_is_stated(self) -> None:
        for title in (
            "Indicate the type of firm and legal structure",
            "Describe the form of business organization",
        ):
            self.assertTrue(
                is_duplicate_static_rfp_section(
                    title, static_section_text=STATIC_ANSWERS_ENTITY_ONLY
                ),
                title,
            )
            self.assertFalse(
                is_duplicate_static_rfp_section(
                    title, static_section_text=STATIC_ANSWERS_YEAR_ONLY
                ),
                f"{title} (static text states the year, never the entity type)",
            )
            self.assertFalse(is_duplicate_static_rfp_section(title), title)

    def test_founding_phrasings_are_caught_only_when_the_date_is_stated(self) -> None:
        for title in (
            "State the year the firm was established",
            "Number of years in business",
        ):
            self.assertTrue(
                is_duplicate_static_rfp_section(
                    title, static_section_text=STATIC_ANSWERS_YEAR_ONLY
                ),
                title,
            )
            self.assertFalse(
                is_duplicate_static_rfp_section(
                    title, static_section_text=STATIC_ANSWERS_NEITHER
                ),
                title,
            )
            self.assertFalse(is_duplicate_static_rfp_section(title), title)

    def test_an_open_ended_description_ask_is_never_auto_satisfied(self) -> None:
        """"Provide a brief description of the company" enumerates nothing, so
        there is no answer shape to look for. It fails closed even against static
        text that answers every other ask in this file - a length-based proxy
        would have passed it and re-created the original defect."""
        title = "Provide a brief description of the company"
        for static_text in (
            STATIC_ANSWERS_ENTITY_AND_YEAR,
            STATIC_ANSWERS_ENTITY_ONLY,
            STATIC_ANSWERS_YEAR_ONLY,
            STATIC_ANSWERS_NEITHER,
        ):
            self.assertFalse(
                is_duplicate_static_rfp_section(title, static_section_text=static_text),
                static_text,
            )
        self.assertFalse(is_duplicate_static_rfp_section(title))

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
