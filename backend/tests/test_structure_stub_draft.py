"""Structure stubs must be draftable; Team Qualifications is not a reference tab."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_draft_structure_stubs import (
    _stub_draft_brief,
    section_is_rfp_draft_stub,
    section_needs_presubmit_fill,
)
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

    def test_performance_stub_needs_presubmit_fill(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            _stub_draft_brief,
            section_needs_presubmit_fill,
        )

        sec = ProposalSection(
            id="rfp-structure-performance-and-outcome-indicators",
            title="Performance and Outcome Indicators",
            content=(
                "## Performance and Outcome Indicators\n\n"
                "[MANUAL FILL: Draft this RFP-required section — "
                "Performance and Outcome Indicators]\n\n"
                "RFP instructions: Required in this RFP's submission sequence."
            ),
            status="generated",
            required=True,
            word_target=550,
        )
        self.assertTrue(section_is_rfp_draft_stub(sec))
        self.assertTrue(section_needs_presubmit_fill(sec))
        brief = _stub_draft_brief(sec)
        self.assertIn("unique ask", brief)
        self.assertNotIn("Short bios for principal team members", brief)

    def test_finished_prose_is_not_presubmit_fill(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            section_needs_presubmit_fill,
        )

        sec = ProposalSection(
            id="rfp-sec-work-plan",
            title="Work Plan",
            content=(
                "We will run a 12-week anti-stigma campaign with weekly creative "
                "reviews, paid media flights, and a named project manager. "
                "Kickoff follows award within ten business days."
            ),
            status="generated",
        )
        self.assertFalse(section_needs_presubmit_fill(sec))

    def test_heading_only_licenses_needs_fill(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            _stub_draft_brief,
            section_needs_presubmit_fill,
            stub_fill_landed,
        )

        sec = ProposalSection(
            id="rfp-sec-licenses",
            title="Licenses and Certification",
            content="6. LICENSES AND CERTIFICATION",
            status="generated",
        )
        self.assertTrue(section_needs_presubmit_fill(sec))
        brief = _stub_draft_brief(sec)
        self.assertIn("1.4", brief)
        after = ProposalSection(
            id=sec.id,
            title=sec.title,
            content=(
                "## Licenses and Certification\n\n"
                "Agency licenses and certifications are listed in **Section 1.4** "
                "and insurance coverages in **Section 1.5**. This tab does not "
                "repeat those tables. Attach current certificates of insurance "
                "and any required professional licenses with the submission. "
                "[MANUAL FILL: Sonja — confirm COI on file.]"
            ),
            status="generated",
        )
        self.assertTrue(stub_fill_landed(sec, after))
        self.assertFalse(stub_fill_landed(sec, sec))

    def test_budget_tab_is_not_presubmit_fill(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            section_needs_presubmit_fill,
        )

        sec = ProposalSection(
            id="section-budget-pricing",
            title="Budget & Pricing",
            content="",
            status="generated",
        )
        self.assertFalse(section_needs_presubmit_fill(sec))


if __name__ == "__main__":
    unittest.main()
