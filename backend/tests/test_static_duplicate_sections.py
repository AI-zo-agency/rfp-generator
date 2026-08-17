"""Static-duplicate RFP tabs must not be selected during intelligence / Phase 3."""

from __future__ import annotations

import unittest

from app.services.proposal_voice_enforcement import (
    is_duplicate_static_rfp_section,
    should_skip_rfp_section_as_static_duplicate,
)


class StaticDuplicateSectionTests(unittest.TestCase):
    def test_company_history_roster_is_duplicate(self) -> None:
        self.assertTrue(
            is_duplicate_static_rfp_section(
                "Company History, Core Services, and Client Roster"
            )
        )

    def test_who_we_are_is_duplicate(self) -> None:
        self.assertTrue(is_duplicate_static_rfp_section("Who We Are"))

    def test_company_background_is_duplicate(self) -> None:
        self.assertTrue(is_duplicate_static_rfp_section("Company Background"))

    def test_sample_work_portfolio_is_kept(self) -> None:
        self.assertFalse(
            is_duplicate_static_rfp_section(
                "Sample Work Portfolio (Minimum Two Recent Campaigns)"
            )
        )

    def test_agency_requirements_kept(self) -> None:
        self.assertFalse(
            is_duplicate_static_rfp_section(
                "Agency Requirements — Media Planning & Buying"
            )
        )

    def test_duplicate_of_static_field_skips(self) -> None:
        self.assertTrue(
            should_skip_rfp_section_as_static_duplicate(
                title="Firm Background Narrative",
                duplicate_of_static_section="section-1",
            )
        )

    def test_org_structure_is_duplicate(self) -> None:
        self.assertTrue(is_duplicate_static_rfp_section("Organizational Structure"))

    def test_qualifications_and_experience_not_killed_by_regex(self) -> None:
        # Scored RFP TOC tabs must not be dropped by a qualifications regex.
        self.assertFalse(is_duplicate_static_rfp_section("Qualifications and Experience"))
        self.assertFalse(
            is_duplicate_static_rfp_section(
                "Qualifications and Experience of the Firm and Key Personnel"
            )
        )

    def test_team_overview_with_bios_is_duplicate_of_section_2(self) -> None:
        self.assertTrue(
            is_duplicate_static_rfp_section(
                "Team Overview — Contract Manager, Primary Point of Contact, "
                "and Personnel Bios/Resumes"
            )
        )
        self.assertTrue(
            should_skip_rfp_section_as_static_duplicate(
                title=(
                    "Team Overview — Contract Manager, Primary Point of Contact, "
                    "and Personnel Bios/Resumes"
                ),
            )
        )


    def test_certificate_of_insurance_is_static_duplicate(self) -> None:
        self.assertTrue(is_duplicate_static_rfp_section("Certificate of Insurance"))
        self.assertTrue(
            should_skip_rfp_section_as_static_duplicate(
                title="Certificate of Insurance upon contract execution",
            )
        )
        # Must not be kept just because insurance is an "important" closing topic.
        from app.services.proposal_outline_dedup import filter_lean_outline_sections

        kept, dropped = filter_lean_outline_sections(
            [
                {"id": "coi", "title": "Certificate of Insurance", "required": True},
                {"id": "approach", "title": "Proposed Approach", "required": True},
            ],
            rfp_context="certificate of insurance proposed approach",
        )
        titles = [s["title"] for s in kept]
        self.assertNotIn("Certificate of Insurance", titles)
        self.assertTrue(any("owned by Sections 1" in d for d in dropped))


if __name__ == "__main__":
    unittest.main()
