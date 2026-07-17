import unittest

from app.services.proposal_sections_graph import (
    _apply_verified_corrections,
    _format_member_bio_content,
    _is_member_bio_file_hit,
    _normalize_selected_bio_members,
    _parse_bio_sections_from_text,
    _prefer_full_bio_text,
    _sanitize_content,
    _section_payload,
)


class ProposalBioTests(unittest.TestCase):
    def test_missing_bio_details_do_not_invent_credentials(self) -> None:
        content = _format_member_bio_content("Test Person", {})

        self.assertIn("**Description of Member**", content)
        self.assertIn("[VERIFY: Description of Member]", content)
        self.assertIn("**Years of Experience**", content)
        self.assertIn("[VERIFY: Years of Experience]", content)
        self.assertIn("**Education**", content)
        self.assertIn("[VERIFY: Education]", content)
        self.assertNotIn("**Certifications**", content)
        self.assertNotIn("[VERIFY: Certifications]", content)
        self.assertNotIn("**Licenses**", content)
        self.assertIn("**Work History**", content)
        self.assertIn("[VERIFY: Work History]", content)
        self.assertIn("**Key Accounts**", content)
        self.assertIn("[VERIFY: Key Accounts]", content)

    def test_real_certifications_and_licenses_are_rendered(self) -> None:
        content = _format_member_bio_content(
            "Test Person",
            {
                "certifications": ["Google Project Management"],
                "licenses": ["Oregon Principal Broker License"],
            },
        )

        self.assertIn("**Certifications**", content)
        self.assertIn("- Google Project Management", content)
        self.assertIn("**Licenses**", content)
        self.assertIn("- Oregon Principal Broker License", content)

    def test_certifications_header_is_parsed_from_approved_bio(self) -> None:
        text = """# TODD ANDERSON
executive director

Todd leads the agency.

# CERTIFICATIONS

State Teaching License (PK-12)

# KEY ACCOUNTS

HAMPTON LUMBER
"""
        parsed = _parse_bio_sections_from_text(text, "Todd Anderson")

        self.assertEqual(parsed["certifications"], ["State Teaching License (PK-12)"])

    def test_certifications_stop_at_next_markdown_heading(self) -> None:
        text = """## TODD ANDERSON
executive director

Todd brings steady leadership to the agency.

## CERTIFICATIONS

State Teaching License (PK-12)

## WORK HISTORY

zö agency
Executive Director
2013 - Present

## KEY ACCOUNTS

HAMPTON LUMBER
"""
        parsed = _parse_bio_sections_from_text(text, "Todd Anderson")

        self.assertEqual(parsed["certifications"], ["State Teaching License (PK-12)"])
        self.assertEqual(parsed["key_accounts"], ["HAMPTON LUMBER"])
        self.assertNotIn("WORK HISTORY", " ".join(parsed["certifications"]))

    def test_logo_descriptions_supply_key_account_names(self) -> None:
        text = """# TODD ANDERSON
executive director

Todd leads the agency.

# KEY ACCOUNTS

> **[logo]** A logo for Hampton Lumber, featuring a stylized letter H.

> **[logo]** A logo featuring the word 'Torrent' in blue text.
"""
        parsed = _parse_bio_sections_from_text(text, "Todd Anderson")

        self.assertEqual(parsed["key_accounts"], ["Hampton Lumber", "Torrent"])

    def test_work_history_splits_company_title_and_dates(self) -> None:
        text = """# TODD ANDERSON
executive director

Todd leads the agency.

# WORK HISTORY

**zö agency**
2013 - Present

**North Kitsap School District**
Long-Term Substitute & Full-Time Substitute
Teacher, 2019 - Present
"""
        parsed = _parse_bio_sections_from_text(text, "Todd Anderson")

        self.assertEqual(
            parsed["work_history"],
            [
                {
                    "company": "zö agency",
                    "title": "[VERIFY: title]",
                    "dates": "2013 - Present",
                },
                {
                    "company": "North Kitsap School District",
                    "title": "Long-Term Substitute & Full-Time Substitute Teacher",
                    "dates": "2019 - Present",
                },
            ],
        )

    def test_selected_bios_are_at_most_five_and_deduplicated(self) -> None:
        selected = _normalize_selected_bio_members(
            [
                "Sonja Anderson",
                "Rachael Rice",
                "Rachel Rice",
                "Todd Anderson",
                "Ron Comer",
                "Curt Schultz",
                "Gil Aranowitz",
            ]
        )

        self.assertLessEqual(len(selected), 5)
        self.assertEqual(selected[0], "Sonja Anderson")
        self.assertEqual(len({name.split()[-1].lower() for name in selected}), len(selected))

    def test_selected_bios_no_mandatory_defaults(self) -> None:
        selected = _normalize_selected_bio_members(["Gil Aranowitz"])
        self.assertEqual(selected, ["Gil Aranowitz"])

    def test_selected_bios_correct_known_garbled_names(self) -> None:
        selected = _normalize_selected_bio_members(
            [
                "Ron Corner",
                "Dyetola Doyewunmi",
                "Shawn DiCrisio",
            ]
        )

        self.assertIn("Ron Comer", selected)
        self.assertIn("Oyetola Oyewunmi", selected)
        self.assertIn("Shawn DiCriscio", selected)
        self.assertNotIn("Ron Corner", selected)
        self.assertNotIn("Dyetola Doyewunmi", selected)
        self.assertNotIn("Shawn DiCrisio", selected)

    def test_generated_content_corrects_known_garbled_names(self) -> None:
        content = (
            "Ron Corner will work with Dyetola Doyewunmi and Shawn DiCrisio."
        )

        corrected = _apply_verified_corrections(content)

        self.assertEqual(
            corrected,
            "Ron Comer will work with Oyetola Oyewunmi and Shawn DiCriscio.",
        )

    def test_section_payload_corrects_names_outside_bio_sections(self) -> None:
        section = _section_payload(
            section_id="section-1-org-structure",
            title="1.2 — Organizational Structure",
            mode="pull",
            word_target=100,
            page_limit=30,
            page_ratio=0.04,
            designer_note_default="",
            raw={"content": "Ron Corner, Dyetola Doyewunmi, Shawn DiCrisio"},
            kb_sources=[],
        )

        self.assertEqual(
            section["content"],
            "Ron Comer, Oyetola Oyewunmi, Shawn DiCriscio",
        )

    def test_bio_hit_must_match_exact_person_not_shared_surname(self) -> None:
        sonja_hit = {"metadata": {"fileName": "04_Bio_SonjaAnderson.pdf"}}
        todd_hit = {"metadata": {"fileName": "04_Bio_ToddAnderson.pdf"}}

        self.assertFalse(_is_member_bio_file_hit(sonja_hit, "Todd Anderson"))
        self.assertTrue(_is_member_bio_file_hit(todd_hit, "Todd Anderson"))

    def test_full_bio_document_always_wins_over_longer_search_context(self) -> None:
        self.assertEqual(
            _prefer_full_bio_text("approved full bio", "long search text " * 100),
            "approved full bio",
        )

    def test_sanitize_strips_internal_source_citations(self) -> None:
        text = (
            "Why Relevant\n\n"
            "This campaign proves we can unify festival messaging.\n\n"
            "*Source: 11_REF_CaseStudyMaster_2025.docx*\n"
        )
        cleaned = _sanitize_content(text)
        self.assertNotIn("Source:", cleaned)
        self.assertNotIn("11_REF_CaseStudyMaster_2025.docx", cleaned)
        self.assertIn("Why Relevant", cleaned)


if __name__ == "__main__":
    unittest.main()
