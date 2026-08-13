"""Capability past-proven scrub + bio year grounding."""

from __future__ import annotations

import unittest

from app.services.proposal_capability_bio_grounding import (
    align_bio_years_deterministically,
    bio_adds_ungrounded_specialization,
    bio_block_is_role_only,
    bio_years_inflated_vs_kb,
    is_personnel_bio_section,
    named_people_in_section,
    section_asserts_past_proven_capability,
    section_or_instruction_needs_bio_kb,
)
from app.services.proposal_chat_content_repair import user_asks_content_risk_repair
from app.services.proposal_chat_ops import classify_chat_op


class CapabilityDetectionTests(unittest.TestCase):
    def test_past_proven_table_row_detected(self) -> None:
        text = (
            "| IEUA Requirement | zö Experience |\n"
            "| --- | --- |\n"
            "| Permissions | We have implemented enterprise WordPress installations "
            "with granular user permissions for government clients |\n"
        )
        self.assertTrue(section_asserts_past_proven_capability(text))

    def test_can_deliver_not_flagged_as_past_proven(self) -> None:
        text = (
            "We can deliver WordPress role-based permissions and integrate with "
            "municipal platforms as part of this engagement."
        )
        self.assertFalse(section_asserts_past_proven_capability(text))


class BioYearGroundingTests(unittest.TestCase):
    def test_inflated_years_detected(self) -> None:
        draft = (
            "Shawn has 12 years of WordPress development experience specializing in "
            "government and municipal websites."
        )
        kb = (
            "Shawn DiCriscio — 10 years of WordPress development experience. "
            "Specializes in WordPress."
        )
        self.assertTrue(bio_years_inflated_vs_kb(draft, kb))
        self.assertTrue(bio_adds_ungrounded_specialization(draft, kb))
        fixed, logs = align_bio_years_deterministically(draft, kb)
        self.assertTrue(logs)
        self.assertIn("10 years", fixed)
        self.assertNotIn("12 years", fixed)

    def test_kb_category_years_not_treated_as_missing(self) -> None:
        draft = "Sonja brings Management 30 years and Creative 20 years."
        kb = "Management: 30 years. Creative: 20 years. Horizon Broadcasting 2004."
        self.assertFalse(bio_years_inflated_vs_kb(draft, kb))


class PersonnelSectionBioTests(unittest.TestCase):
    def test_experience_of_personnel_is_personnel_section(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="rfp-sec-23",
            title="23 Experience of Personnel",
            content="### Shawn DiCriscio\n**Role on this engagement:** Lead WordPress developer.",
        )
        self.assertTrue(is_personnel_bio_section(section))
        self.assertIn("Shawn DiCriscio", named_people_in_section(section))
        self.assertTrue(bio_block_is_role_only(section.content or "", "Shawn DiCriscio"))

    def test_role_plus_kb_paragraph_is_not_role_only(self) -> None:
        text = (
            "### Shawn DiCriscio\n"
            "**Role on this engagement:** Lead WordPress developer.\n\n"
            "Shawn has 12 years of WordPress development experience and has built "
            "hundreds of websites across a wide range of styles and markets."
        )
        self.assertFalse(bio_block_is_role_only(text, "Shawn DiCriscio"))

    def test_comma_title_heading_parses_name(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="rfp-sec-23",
            title="23 Experience of Personnel",
            content=(
                "### Shawn DiCriscio, Senior Web Developer\n"
                "**Role on this engagement:** Lead WordPress developer.\n"
            ),
        )
        self.assertIn("Shawn DiCriscio", named_people_in_section(section))

    def test_user_message_names_person_when_heading_odd(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="rfp-sec-23",
            title="23 Experience of Personnel",
            content=(
                "### Shawn DiCriscio, Senior Web Developer\n"
                "**Role on this engagement:** Lead.\n"
            ),
        )
        names = named_people_in_section(
            section,
            user_message=(
                "here in shawn DiCriscio make sure you fetch correct info "
                "from its resume and update it"
            ),
        )
        self.assertIn("Shawn DiCriscio", names)

    def test_personnel_tab_needs_bio_kb_even_without_bio_in_instruction(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="rfp-sec-23",
            title="23 Experience of Personnel",
            content="### Shawn DiCriscio\n**Role on this engagement:** Lead developer.",
        )
        self.assertTrue(section_or_instruction_needs_bio_kb(section, "Apply the fix"))
        self.assertTrue(
            section_or_instruction_needs_bio_kb(
                section,
                "Shawn's invented government specialization — fetch correct info",
            )
        )

    def test_business_info_email_fix_does_not_need_bio_kb(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="section-1-business-info",
            title="1.3 — Business Information",
            content="| Email | info@zo.agency |",
        )
        self.assertFalse(
            section_or_instruction_needs_bio_kb(
                section,
                "Change Email from info@zo.agency to connect@zo.agency per 01_companyfacts.",
            )
        )


class ChatIntentTests(unittest.TestCase):
    def test_remove_fabricated_capability_triggers_purge(self) -> None:
        kind = classify_chat_op(
            "remove fabricated capability claims and inflated bio years"
        )
        # Either path runs the capability/bio grounding scrub.
        self.assertIn(kind, {"remove_fabricated", "fix_content_risks"})

    def test_explicit_remove_fabricated_content_phrase(self) -> None:
        self.assertEqual(
            classify_chat_op("remove fabricated content across the proposal"),
            "remove_fabricated",
        )

    def test_pasted_audit_triggers_content_risk(self) -> None:
        msg = (
            "Content issues that still matter:\n"
            "1. Section 15 repeats unverified capability claims about WordPress\n"
            "2. Shawn bio inflated years and government specialization\n"
            "3. Case studies don't match the sector claim\n"
            "Please fix these content risks across the proposal."
        )
        self.assertTrue(user_asks_content_risk_repair(msg))


if __name__ == "__main__":
    unittest.main()
