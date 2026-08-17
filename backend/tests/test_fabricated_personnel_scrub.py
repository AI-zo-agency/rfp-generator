"""Fabricated personnel scrub on manuscript drafts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.evidence_trust.personnel_grounding import (
    scrub_fabricated_personnel_from_draft,
)
from app.services.proposal_fulfill_rfp_repairs import apply_deterministic_roster_fixes


class FabricatedPersonnelScrubTests(unittest.TestCase):
    def test_removes_known_fabricated_names(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="bios",
                    title="Team",
                    content="Creative Director: Brittany Frazier. PM: Drew Stone.",
                )
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Brittany Frazier", body)
        self.assertNotIn("Drew Stone", body)
        self.assertTrue(logs)

    def test_removes_murilo_mendes_keeps_marcelle(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-2",
                    title="1.2 — Organizational Structure",
                    content=(
                        "Kelvin Kiruthu Senior Graphic Designer "
                        "Murilo Mendes Graphic Designer "
                        "Miguel Perez Production Designer "
                        "Marcelle Benevides Graphic Designer"
                    ),
                )
            ],
        )
        updated, logs = scrub_fabricated_personnel_from_draft(draft)
        body = updated.sections[0].content or ""
        self.assertNotIn("Murilo Mendes", body)
        self.assertIn("Marcelle Benevides", body)
        self.assertIn("Kelvin Kiruthu", body)
        self.assertTrue(logs)

    def test_roster_fix_replaces_murilo_with_marcelle(self) -> None:
        text = "Kelvin Kiruthu Senior Graphic Designer Murilo Mendes Graphic Designer"
        fixed, logs = apply_deterministic_roster_fixes(text)
        self.assertTrue(logs)
        self.assertNotIn("Murilo Mendes", fixed)
        self.assertIn("Marcelle Benevides", fixed)


if __name__ == "__main__":
    unittest.main()
