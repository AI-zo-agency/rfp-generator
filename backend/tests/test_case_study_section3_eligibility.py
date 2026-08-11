"""Section 3 must not pick All-Case-Studies dumps or org-structure templates."""

from __future__ import annotations

import unittest

from app.services.proposal_case_study_eligibility import (
    is_eligible_section3_case_study_title,
)


class Section3CaseStudyEligibilityTests(unittest.TestCase):
    def test_rejects_all_case_studies_master_dump(self) -> None:
        self.assertFalse(
            is_eligible_section3_case_study_title(
                "Copy of 03 CS All Case Studies_LastUpdate2026-05-03"
            )
        )
        self.assertFalse(
            is_eligible_section3_case_study_title("03_CS_AllCaseStudies.pdf")
        )

    def test_rejects_org_structure_master_template(self) -> None:
        self.assertFalse(
            is_eligible_section3_case_study_title("02_MasterTemplate_OrgStructure")
        )
        self.assertFalse(
            is_eligible_section3_case_study_title(
                "Master Template Org Structure All Team Bios"
            )
        )

    def test_accepts_real_single_project_case_study(self) -> None:
        self.assertTrue(
            is_eligible_section3_case_study_title("03_CS_CityOfMedford_RogueX.pdf")
        )
        self.assertTrue(
            is_eligible_section3_case_study_title("Oregon Employment Geofencing")
        )

    def test_rejects_infinite_assets_on_civic_rfp(self) -> None:
        self.assertFalse(
            is_eligible_section3_case_study_title(
                "Infinite Assets Verbal and Visual Brand Identity",
                rfp_title="Public Education Campaign for NYC Charter Ballot Items",
                rfp_sector="Government / Civic",
            )
        )
        self.assertTrue(
            is_eligible_section3_case_study_title(
                "Infinite Assets Verbal and Visual Brand Identity",
                rfp_title="Financial advisory personal branding",
                rfp_sector="Professional Services",
            )
        )


if __name__ == "__main__":
    unittest.main()
