"""Ineligible Section 3 dumps must not block regeneration."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_generator import _section3_card_is_usable


class Section3UsableCardTests(unittest.TestCase):
    def test_all_case_studies_dump_is_not_usable(self) -> None:
        sec = ProposalSection(
            id="section-3-work-01-copy-of-03_cs_allcasestudies_lastupdate2",
            title="3.1 — Copy of 03 CS All Case Studies Last Update2026 05 03",
            content="RELEVANT CASE STUDIES\nCITY OF MEDFORD\n…",
            status="generated",
            source="generated",
        )
        self.assertFalse(_section3_card_is_usable(sec))

    def test_org_structure_template_is_not_usable(self) -> None:
        sec = ProposalSection(
            id="section-3-work-02-02_mastertemplate_orgstructure",
            title="3.2 — Master Template Org Structure",
            content="Challenge\nHampton Lumber needed…",
            status="generated",
            source="generated",
        )
        self.assertFalse(_section3_card_is_usable(sec))

    def test_real_medford_case_study_is_usable(self) -> None:
        sec = ProposalSection(
            id="section-3-work-01-03_cs_cityofmedford_roguex",
            title="3.1 — City of Medford Rogue X",
            content=(
                "Challenge\nMedford needed a brand for Rogue X.\n\n"
                "Solution / Our Approach\nWe built Community Thrives Here.\n"
            ),
            status="generated",
            source="generated",
        )
        self.assertTrue(_section3_card_is_usable(sec))


if __name__ == "__main__":
    unittest.main()
