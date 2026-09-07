"""Instruction / eligibility RFP sentences must never become proposal tabs.

Same contamination class as Ontario: packaging rules, drafting directions, and
DQ warnings were minted as Section 23/25/35 and then "Generating…".
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    _row_to_rfp_section_spec,
    _spec_is_non_deliverable,
    _title_is_non_deliverable,
    drop_non_deliverable_rfp_sections,
    outline_sections_from_rfp_specs,
)
from app.services.proposal_outline_dedup import (
    filter_lean_outline_sections,
    humanize_outline_title,
)


_COST_FILE_INSTRUCTION = (
    "DO NOT INCLUDE A COPY OF YOUR COST FILE WITH THE MAIN PROPOSAL"
)
# Truncated mid-sentence forms (humanize / UI) stay under the >85-char noise gate
# but must still be rejected as non-deliverable.
_EXCEPTIONS_DRAFTING = (
    "If any exceptions are taken, this Statement of Compliance shall include"
)
_DQ_WARNING = (
    "Unsatisfactory references or unsatisfactory work performance may eliminate"
)
# Live drafts stamp outline numbers onto the tab title — detector must still hit.
_NUMBERED_COST_FILE = f"23. {_COST_FILE_INSTRUCTION}"
_NUMBERED_EXCEPTIONS = f"25. {_EXCEPTIONS_DRAFTING}"
_NUMBERED_DQ = f"35. {_DQ_WARNING}"


class InstructionTitleNotDeliverableTests(unittest.TestCase):
    def test_packaging_instruction_is_non_deliverable(self) -> None:
        self.assertTrue(_title_is_non_deliverable(_COST_FILE_INSTRUCTION))
        self.assertTrue(_title_is_non_deliverable(_NUMBERED_COST_FILE))
        self.assertTrue(
            _spec_is_non_deliverable(RfpSectionSpec(rfp_title=_COST_FILE_INSTRUCTION))
        )

    def test_exceptions_drafting_direction_is_non_deliverable(self) -> None:
        self.assertTrue(_title_is_non_deliverable(_EXCEPTIONS_DRAFTING))
        self.assertTrue(_title_is_non_deliverable(_NUMBERED_EXCEPTIONS))

    def test_eligibility_warning_is_non_deliverable(self) -> None:
        self.assertTrue(_title_is_non_deliverable(_DQ_WARNING))
        self.assertTrue(_title_is_non_deliverable(_NUMBERED_DQ))

    def test_recover_statement_of_compliance_from_exceptions_instruction(self) -> None:
        from app.services.proposal_fulfill_rfp_structure import (
            recover_deliverable_title_from_instruction,
        )

        recovered = recover_deliverable_title_from_instruction(_EXCEPTIONS_DRAFTING)
        self.assertEqual(recovered.casefold(), "statement of compliance")
        self.assertEqual(
            recover_deliverable_title_from_instruction(_NUMBERED_EXCEPTIONS).casefold(),
            "statement of compliance",
        )
        # Packaging / DQ warnings do not invent a fake deliverable tab.
        self.assertIsNone(recover_deliverable_title_from_instruction(_COST_FILE_INSTRUCTION))
        self.assertIsNone(recover_deliverable_title_from_instruction(_DQ_WARNING))

    def test_row_parser_recovers_compliance_form_instead_of_dropping(self) -> None:
        spec = _row_to_rfp_section_spec(
            {"rfpTitle": _EXCEPTIONS_DRAFTING},
            mandated_submission_format=True,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.rfp_title.casefold(), "statement of compliance")
        self.assertIn("exceptions", (spec.instructions or "").casefold())

    def test_row_parser_still_rejects_pure_packaging(self) -> None:
        self.assertIsNone(
            _row_to_rfp_section_spec(
                {"rfpTitle": _COST_FILE_INSTRUCTION},
                mandated_submission_format=True,
            )
        )

    def test_real_deliverable_tabs_kept(self) -> None:
        for title in (
            "Statement of Compliance",
            "Fee Proposal",
            "Cost Proposal",
            "References",
            "BIDDER/PROPOSER INFORMATION FORM",
            "AFFIDAVIT OF NON-COLLUSION AND NON-DISCRIMINATION",
            "PROPOSER REQUIRED QUESTIONNAIRE",
            "RESPONSE FILE",
            "Exceptions to RFP (if any)",
            "Cover Letter",
            "Provide monthly performance dashboards and quarterly reviews",
        ):
            self.assertFalse(
                _title_is_non_deliverable(title),
                msg=f"false positive on deliverable: {title!r}",
            )

    def test_row_parser_rejects_instruction_titles(self) -> None:
        for title in (_COST_FILE_INSTRUCTION, _DQ_WARNING):
            spec = _row_to_rfp_section_spec(
                {"rfpTitle": title},
                mandated_submission_format=True,
            )
            self.assertIsNone(spec, msg=title)

    def test_outline_from_specs_skips_instructions(self) -> None:
        specs = [
            RfpSectionSpec(rfp_title="Statement of Compliance"),
            RfpSectionSpec(rfp_title=_COST_FILE_INSTRUCTION),
            RfpSectionSpec(rfp_title=_EXCEPTIONS_DRAFTING),
            RfpSectionSpec(rfp_title="References"),
            RfpSectionSpec(rfp_title=_DQ_WARNING),
        ]
        out = outline_sections_from_rfp_specs(specs, section_factory=lambda raw: raw)
        titles = [s["title"] for s in out]
        # Exceptions instruction recovers to Statement of Compliance (deduped).
        self.assertEqual(titles, ["Statement of Compliance", "References"])

    def test_filter_lean_drops_instruction_titles(self) -> None:
        sections = [
            {"id": "a", "title": "Cover Letter", "required": True},
            {"id": "b", "title": _COST_FILE_INSTRUCTION, "required": True},
            {"id": "c", "title": _EXCEPTIONS_DRAFTING, "required": True},
            {"id": "d", "title": _DQ_WARNING, "required": True},
            {"id": "e", "title": "References", "required": True},
        ]
        kept, dropped = filter_lean_outline_sections(sections, rfp_context="")
        titles = [s["title"] for s in kept]
        self.assertEqual(titles, ["Cover Letter", "Statement of Compliance", "References"])
        self.assertGreaterEqual(len(dropped), 2)

    def test_humanize_refuses_instruction_titles(self) -> None:
        """Do not truncate packaging/DQ prose into a fake TOC label."""
        self.assertEqual(humanize_outline_title(_COST_FILE_INSTRUCTION), "")
        self.assertEqual(
            humanize_outline_title(_EXCEPTIONS_DRAFTING).casefold(),
            "statement of compliance",
        )
        self.assertEqual(humanize_outline_title(_DQ_WARNING), "")
        self.assertEqual(humanize_outline_title("Cover Letter"), "Cover Letter")

    def test_research_cache_scrub_drops_instruction_maps(self) -> None:
        from app.models.proposal import ProposalResearchCache, RfpSectionMap
        from app.services.proposal_fulfill_rfp_structure import (
            scrub_non_deliverable_titles_from_research,
        )

        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            rfpSections=[
                RfpSectionMap(id="a", title="Cover Letter"),
                RfpSectionMap(id="b", title=_COST_FILE_INSTRUCTION),
                RfpSectionMap(id="c", title=_DQ_WARNING),
                RfpSectionMap(id="d", title="References"),
            ],
        )
        cleaned, logs = scrub_non_deliverable_titles_from_research(research)
        titles = [m.title for m in cleaned.rfp_sections]
        self.assertEqual(titles, ["Cover Letter", "References"])
        self.assertTrue(logs)


if __name__ == "__main__":
    unittest.main()
