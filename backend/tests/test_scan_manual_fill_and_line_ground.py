"""Deterministic MANUAL FILL / placeholder scrub for Complete & Clean."""

from __future__ import annotations

import unittest

from app.services.proposal_verify_optional_scrub import (
    strip_manual_fill_tags_not_required_by_rfp,
    strip_placeholder_tags_not_required_by_rfp,
)


class ManualFillScrubTests(unittest.TestCase):
    def test_removes_optional_manual_fill_when_rfp_silent(self) -> None:
        body = (
            "Approach continues as scoped.\n"
            "[MANUAL FILL: Sonja — optional dashboard screenshot]\n"
            "Timeline stays as drafted."
        )
        rfp = "Vendor shall describe technical approach and timeline."
        cleaned, removed = strip_manual_fill_tags_not_required_by_rfp(body, rfp)
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("[MANUAL FILL", cleaned)
        self.assertIn("Timeline stays", cleaned)

    def test_keeps_insurance_manual_fill_when_rfp_requires_coi(self) -> None:
        body = (
            "Insurance: [MANUAL FILL: Sonja — confirm coverage type & limits on current COI]"
        )
        rfp = (
            "Offeror shall provide Certificate of Insurance showing liability coverage "
            "meeting the minimums in Section 1.5."
        )
        cleaned, removed = strip_manual_fill_tags_not_required_by_rfp(body, rfp)
        self.assertEqual(removed, 0)
        self.assertIn("[MANUAL FILL", cleaned)

    def test_keeps_section_draft_stub_manual_fill(self) -> None:
        body = (
            "[MANUAL FILL: Draft this RFP-required section from KB — do not invent]"
        )
        rfp = "Vendor shall submit a Project Schedule."
        cleaned, removed = strip_manual_fill_tags_not_required_by_rfp(body, rfp)
        self.assertEqual(removed, 0)
        self.assertIn("[MANUAL FILL: Draft this RFP-required section", cleaned)

    def test_placeholder_strip_handles_both_tag_types(self) -> None:
        body = (
            "Partners: [VERIFY: backup mobile partner name]\n"
            "Extras: [MANUAL FILL: designer note sample graphic]\n"
            "Done."
        )
        rfp = "Describe the technical approach."
        cleaned, removed = strip_placeholder_tags_not_required_by_rfp(body, rfp)
        self.assertGreaterEqual(removed, 2)
        self.assertNotIn("[VERIFY", cleaned)
        self.assertNotIn("[MANUAL FILL", cleaned)
        self.assertIn("Done.", cleaned)


class LineGroundCanonicalTests(unittest.TestCase):
    def test_canonical_company_excerpt_picks_who_we_are(self) -> None:
        from app.models.proposal import ProposalSection
        from app.services.proposal_scan_line_grounding import _canonical_company_excerpt

        sections = [
            ProposalSection(
                id="s1",
                title="Approach",
                content="We will deliver the work.",
            ),
            ProposalSection(
                id="s2",
                title="Who We Are",
                content=(
                    "zö agency is a creative firm. Legal name: Zo Agency LLC. "
                    "Primary contact listed in companyfacts."
                ),
            ),
        ]
        excerpt = _canonical_company_excerpt(sections)
        self.assertIn("Who We Are", excerpt)
        self.assertIn("Zo Agency LLC", excerpt)


if __name__ == "__main__":
    unittest.main()
