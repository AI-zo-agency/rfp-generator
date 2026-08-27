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

    def test_cover_letter_checklist_needs_letter_body_fill(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            cover_letter_lacks_letter_body,
            is_cover_letter_section_title,
        )

        self.assertTrue(is_cover_letter_section_title("Section 1 - Cover Letter"))
        checklist = (
            "[DESIGNER NOTE: Attach the physically signed cover letter PDF.]\n\n"
            "## COVER LETTER REQUIREMENTS\n\n"
            "THE RFP MANDATES A SIGNED COVER LETTER ADDRESSING FIVE ELEMENTS:\n\n"
            "1. Statement of Intent\n"
            "2. Authorized Signature\n"
            "3. Contact Information\n"
            "4. Addenda Acknowledgement\n"
        )
        self.assertTrue(cover_letter_lacks_letter_body(checklist))
        sec = ProposalSection(
            id="rfp-cover",
            title="Section 1 - Cover Letter",
            content=checklist,
            status="generated",
        )
        self.assertTrue(section_is_rfp_draft_stub(sec))
        self.assertTrue(section_needs_presubmit_fill(sec))
        brief = _stub_draft_brief(sec)
        self.assertIn("real offer letter", brief.casefold())

        real = (
            "Dear Tseng College Selection Committee,\n\n"
            "We are pleased to submit this proposal for Paid Media Campaigns.\n\n"
            "Sincerely,\nSonja Anderson\n"
            "[MANUAL FILL: authorized signature]\n"
            "[DESIGNER NOTE: Attach signed PDF]\n"
        )
        self.assertFalse(cover_letter_lacks_letter_body(real))
        self.assertFalse(
            section_is_rfp_draft_stub(
                ProposalSection(id="rfp-cover", title="Cover Letter", content=real)
            )
        )

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


class SpecCoveredByFilledSectionTests(unittest.TestCase):
    """The stub step must not duplicate a section the writer already drafted."""

    def _spec(self, title: str):
        class _S:
            def __init__(s):
                s.rfp_title = title
                s.same_ask_as = []

        return _S()

    def _filled(self, sid: str, title: str) -> ProposalSection:
        body = " ".join(
            ["We coordinate stakeholders and drive economic development through tourism."] * 7
        )
        return ProposalSection(id=sid, title=title, content=body, status="generated")

    def test_long_rfp_title_covered_by_shorter_filled_section(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            _spec_covered_by_filled_section,
        )

        secs = [
            self._filled("7", "7. Strategic Approach and Measurement"),
            self._filled("8", "8. Stakeholder Coordination and Community Partnership"),
        ]
        self.assertTrue(
            _spec_covered_by_filled_section(
                secs, self._spec("Stakeholder Coordination and Economic Development Through Tourism")
            )
        )
        self.assertTrue(
            _spec_covered_by_filled_section(
                secs, self._spec("Strategic Approach to Tourism Promotion and Place Branding")
            )
        )

    def test_genuinely_missing_spec_is_not_suppressed(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            _spec_covered_by_filled_section,
        )

        secs = [self._filled("8", "8. Stakeholder Coordination and Community Partnership")]
        self.assertFalse(
            _spec_covered_by_filled_section(secs, self._spec("Cost Proposal and Fee Schedule"))
        )

    def test_thin_stub_does_not_count_as_coverage(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            _spec_covered_by_filled_section,
        )

        stub = ProposalSection(
            id="x",
            title="Stakeholder Coordination",
            content="[MANUAL FILL: Draft this RFP-required section — Stakeholder Coordination]",
            status="generated",
        )
        self.assertFalse(
            _spec_covered_by_filled_section(
                [stub], self._spec("Stakeholder Coordination and Economic Development")
            )
        )


class RestoreEmptiedSectionsTests(unittest.TestCase):
    """Complete & Clean must never leave a drafted section as an empty stub."""

    FULL = (
        "Kitsap County needs a differentiated tourism brand. Our plan: A. Vision "
        "positions the county as the Pacific gateway; B. Market Analysis shows 40% "
        "of visitors originate in the Seattle metro; C. KPI Targets grow lodging "
        "tax revenue 12% year over year. We run phased campaigns across paid, "
        "owned, and earned channels with measurable outcomes reported each quarter "
        "and a countywide events calendar with direct referral linking."
    )
    STUB = (
        "## Brand Marketing Plan\n\n"
        "[MANUAL FILL: Draft this RFP-required section — Brand Marketing Plan]\n\n"
        "RFP-required outline:\n- A. Vision\n- B. Market Analysis\n"
    )

    def _draft(self, sections):
        from app.models.proposal import ProposalDraft

        return ProposalDraft(rfpId="r", updatedAt="t", sections=sections)

    def _prior(self):
        return [
            ProposalSection(
                id="s4", title="Brand Marketing Plan", content=self.FULL, status="generated"
            )
        ]

    def test_restores_by_id(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        draft = self._draft(
            [ProposalSection(id="s4", title="Brand Marketing Plan", content=self.STUB, status="generated")]
        )
        out, logs = restore_sections_emptied_by_scan(draft, self._prior())
        self.assertEqual(out.sections[0].content, self.FULL)
        self.assertEqual(len(logs), 1)

    def test_restores_by_title_when_id_renamed(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        draft = self._draft(
            [
                ProposalSection(
                    id="rfp-structure-brand-marketing-plan",
                    title="Brand Marketing Plan",
                    content=self.STUB,
                    status="generated",
                )
            ]
        )
        out, logs = restore_sections_emptied_by_scan(draft, self._prior())
        self.assertEqual(out.sections[0].content, self.FULL)

    def test_does_not_duplicate_when_prior_still_present(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        draft = self._draft(
            [
                ProposalSection(id="s4", title="Brand Marketing Plan", content=self.FULL, status="generated"),
                ProposalSection(
                    id="rfp-structure-brand-marketing-plan",
                    title="Brand Marketing Plan",
                    content=self.STUB,
                    status="generated",
                ),
            ]
        )
        out, logs = restore_sections_emptied_by_scan(draft, self._prior())
        # The kept full tab must not be cloned into the twin stub.
        self.assertEqual(out.sections[1].content, self.STUB)
        self.assertEqual(logs, [])

    def test_leaves_substantial_result_untouched(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        restructured = self.FULL + " Additional differentiation detail for Kitsap."
        draft = self._draft(
            [ProposalSection(id="s4", title="Brand Marketing Plan", content=restructured, status="generated")]
        )
        out, logs = restore_sections_emptied_by_scan(draft, self._prior())
        self.assertEqual(out.sections[0].content, restructured)
        self.assertEqual(logs, [])

    def test_restores_good_section_overwritten_by_bio_stub(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        bio_stub = (
            "## Brand Marketing Plan\n\n"
            "**Role on this engagement:** Marketing Lead\n\n"
            "[DESIGNER NOTE: Insert approved bio PDF — 04_Bio_Someone.pdf]"
        )
        draft = self._draft(
            [ProposalSection(id="s4", title="Brand Marketing Plan", content=bio_stub, status="generated")]
        )
        out, logs = restore_sections_emptied_by_scan(draft, self._prior())
        self.assertEqual(out.sections[0].content, self.FULL)
        self.assertEqual(len(logs), 1)

    def test_real_section2_bio_tab_is_never_reverted(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        bio_stub = (
            "## 2.1 — Jane Doe\n\n**Role on this engagement:** Designer\n\n"
            "[DESIGNER NOTE: Insert approved bio PDF — 04_Bio_JaneDoe.pdf]"
        )
        prior = [
            ProposalSection(id="section-2-bio-jane-doe", title="2.1 — Jane Doe", content=self.FULL, status="generated")
        ]
        draft = self._draft(
            [ProposalSection(id="section-2-bio-jane-doe", title="2.1 — Jane Doe", content=bio_stub, status="generated")]
        )
        out, logs = restore_sections_emptied_by_scan(draft, prior)
        # A bio stub is legitimate content for a real Section 2 bio tab.
        self.assertEqual(out.sections[0].content, bio_stub)
        self.assertEqual(logs, [])

    def test_does_not_restore_a_prior_stub(self) -> None:
        from app.services.proposal_draft_structure_stubs import (
            restore_sections_emptied_by_scan,
        )

        prior_stub = [
            ProposalSection(id="s4", title="Brand Marketing Plan", content=self.STUB, status="generated")
        ]
        draft = self._draft(
            [ProposalSection(id="s4", title="Brand Marketing Plan", content=self.STUB, status="generated")]
        )
        out, logs = restore_sections_emptied_by_scan(draft, prior_stub)
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
