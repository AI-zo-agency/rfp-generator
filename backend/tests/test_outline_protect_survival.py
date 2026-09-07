"""Mandated RFP TOC tabs must survive OutlineSection → RfpSectionMap → lean/cap."""

from __future__ import annotations

import unittest

from app.models.proposal import RfpSectionMap
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    outline_sections_from_rfp_specs,
)
from app.services.proposal_outline_dedup import (
    enforce_outline_section_cap,
    filter_lean_outline_sections,
    section_is_rfp_derived,
)


class OutlineProtectSurvivalTests(unittest.TestCase):
    def test_rfp_section_map_carries_protect_from_cap(self) -> None:
        row = RfpSectionMap(
            id="rfp-cert",
            title="12. Certification of Proposal",
            protectFromCap=True,
            submissionInstrument="form",
        )
        self.assertTrue(row.protect_from_cap)
        self.assertEqual(row.submission_instrument, "form")
        self.assertTrue(section_is_rfp_derived(row))

    def test_lean_and_cap_keep_protected_unscored_format_tab(self) -> None:
        sections = [
            RfpSectionMap(
                id="rfp-exec",
                title="1. Executive Summary",
                evaluationWeight=10,
                protectFromCap=True,
            ),
            RfpSectionMap(
                id="rfp-cert",
                title="12. Certification of Proposal",
                evaluationWeight=None,
                protectFromCap=True,
                submissionInstrument="form",
            ),
            RfpSectionMap(
                id="rfp-padding",
                title="Nice To Have Narrative",
                evaluationWeight=None,
                protectFromCap=False,
            ),
        ]
        kept, dropped = filter_lean_outline_sections(
            sections,
            rfp_context="certification of proposal executive summary",
            drop_generic_filler=True,
        )
        titles = [s.title for s in kept]
        self.assertIn("12. Certification of Proposal", titles)
        kept2, cap_dropped = enforce_outline_section_cap(kept, 2)
        titles2 = [s.title for s in kept2]
        self.assertIn("12. Certification of Proposal", titles2)
        self.assertTrue(
            any("Nice To Have" in d or "padding" in d.casefold() for d in dropped + cap_dropped)
            or "Nice To Have Narrative" not in titles2
        )

    def test_satisfied_by_static_stamp_does_not_drop_bare_insurance(self) -> None:
        """Extractor over-marks late TOC rows; bare Insurance must still mint."""

        def factory(raw: dict) -> dict:
            return raw

        specs = [
            RfpSectionSpec(
                rfp_title="9. Insurance",
                satisfied_by_static_company_block=True,
            ),
            RfpSectionSpec(
                rfp_title="Company Background",
                satisfied_by_static_company_block=True,
            ),
            RfpSectionSpec(
                rfp_title="12. Certification of Proposal",
                satisfied_by_static_company_block=False,
            ),
        ]
        sections = outline_sections_from_rfp_specs(specs, section_factory=factory)
        titles = [s["title"] for s in sections]
        self.assertIn("9. Insurance", titles)
        self.assertIn("12. Certification of Proposal", titles)
        self.assertNotIn("Company Background", titles)


if __name__ == "__main__":
    unittest.main()
