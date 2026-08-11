"""Structure stubs must be draftable; Team Qualifications is not a reference tab."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_draft_structure_stubs import section_is_rfp_draft_stub
from app.services.proposal_fulfill_rfp_structure import _title_is_qual_or_reference


class StructureStubDraftTests(unittest.TestCase):
    def test_team_qualifications_is_not_blocked_as_inventable_refs(self) -> None:
        self.assertFalse(
            _title_is_qual_or_reference("B. Respondent Team Qualifications")
        )
        self.assertFalse(
            _title_is_qual_or_reference("Key Personnel Qualifications")
        )
        self.assertTrue(
            _title_is_qual_or_reference("Offeror Qualifications and References")
        )

    def test_detects_draft_this_rfp_stub(self) -> None:
        sec = ProposalSection(
            id="rfp-structure-b-respondent-team-qualifications",
            title="B. Respondent Team Qualifications",
            content=(
                "## B. Respondent Team Qualifications\n\n"
                "[MANUAL FILL: Draft this RFP-required section — B. Respondent Team Qualifications]\n\n"
                "RFP-required outline:\n- Short bios\n"
            ),
            status="generated",
        )
        self.assertTrue(section_is_rfp_draft_stub(sec))


if __name__ == "__main__":
    unittest.main()
