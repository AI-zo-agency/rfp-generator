import unittest

from app.services.proposal_sections_graph import (
    _apply_verified_corrections,
    _dedupe_key_accounts,
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

        self.assertNotIn("**Description of Member**", content)
        self.assertNotIn("[VERIFY:", content)
        self.assertNotIn("**Years of Experience**", content)
        self.assertNotIn("**Education**", content)
        self.assertNotIn("**Certifications**", content)
        self.assertNotIn("**Licenses**", content)
        self.assertNotIn("**Work History**", content)
        self.assertNotIn("**Key Accounts**", content)
        self.assertIn("### Test Person", content)

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

    def test_work_history_parses_en_dash_date_ranges(self) -> None:
        text = """# CURT SCHULTZ
creative director

Curt leads creative work.

# WORK HISTORY

**zö agency**
Creative Director
2013 – Present

**SRIA**
Account lead
2020 — 2024
"""
        parsed = _parse_bio_sections_from_text(text, "Curt Schultz")

        self.assertGreaterEqual(len(parsed["work_history"]), 1)
        dates = " ".join(j.get("dates", "") for j in parsed["work_history"])
        self.assertIn("2013", dates)
        self.assertIn("Present", dates)

    def test_key_accounts_collapse_near_duplicates(self) -> None:
        deduped = _dedupe_key_accounts(
            [
                "Sayfe Families",
                "POA",
                "Sayfe",
                "the Sayfe Families",
                "INTEGRATE. CALIBRATE. VALIDATE.",
            ]
        )
        self.assertEqual(
            deduped,
            ["Sayfe Families", "POA", "INTEGRATE. CALIBRATE. VALIDATE."],
        )

    def test_format_dedupes_repeated_licenses_and_accounts(self) -> None:
        content = _format_member_bio_content(
            "Rachel Rice",
            {
                "licenses": [
                    "California Real Estate License",
                    "Oregon Real Estate Principal Broker License",
                    "California Real Estate License",
                    "Oregon Real Estate Principal Broker License",
                ],
                "key_accounts": [
                    "Sayfe Families",
                    "POA",
                    "Sayfe",
                    "pops",
                ],
            },
        )
        self.assertEqual(content.count("California Real Estate License"), 1)
        self.assertEqual(
            content.count("Oregon Real Estate Principal Broker License"), 1
        )
        self.assertIn("- Sayfe Families", content)
        self.assertNotIn("\n- Sayfe\n", content)

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

    def test_sanitize_does_not_glue_words_across_unicode_spaces(self) -> None:
        # Zero-width / exotic spaces from PDFs used to be dropped → "systemsmust".
        self.assertEqual(
            _sanitize_content("parking systems\u200bmust be seamless"),
            "parking systems must be seamless",
        )
        self.assertEqual(
            _sanitize_content("visitor interaction\u00a0from flight"),
            "visitor interaction from flight",
        )
        # Soft hyphen mid-word should disappear without inserting a space.
        self.assertEqual(
            _sanitize_content("interac\u00adtion from"),
            "interaction from",
        )


if __name__ == "__main__":
    unittest.main()
