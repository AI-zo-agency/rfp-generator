"""Fabricated personnel scrub on manuscript drafts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.evidence_trust.personnel_grounding import (
    scrub_fabricated_personnel_from_draft,
)


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


if __name__ == "__main__":
    unittest.main()
