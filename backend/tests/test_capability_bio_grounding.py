"""Capability past-proven scrub + bio year grounding."""

from __future__ import annotations

import unittest

from app.services.proposal_capability_bio_grounding import (
    align_bio_education_deterministically,
    align_bio_years_deterministically,
    bio_adds_ungrounded_specialization,
    bio_block_is_role_only,
    bio_years_inflated_vs_kb,
    is_personnel_bio_section,
    named_people_in_section,
    person_name_from_tab_title,
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


class BioEducationGroundingTests(unittest.TestCase):
    def test_invented_degree_replaced_from_04_bio(self) -> None:
        draft = (
            "Sonja holds a Bachelor of Microbiology from Federal University of "
            "Oye Ekiti. She leads the agency."
        )
        kb = (
            "Sonja Anderson, Agency Director.\n"
            "Education:\n"
            "Associate of Arts, Grays Harbor College\n\n"
            "Work History:\n"
            "zö agency — Agency Director."
        )
        fixed, logs = align_bio_education_deterministically(
            draft, kb, member="Sonja Anderson"
        )
        self.assertTrue(logs)
        self.assertNotIn("Oye Ekiti", fixed)
        self.assertNotIn("Microbiology", fixed)
        self.assertIn("Grays Harbor", fixed)
        self.assertIn("leads the agency", fixed)

    def test_named_rfp_tab_is_personnel_bio(self) -> None:
        from app.models.proposal import ProposalSection

        section = ProposalSection(
            id="rfp-sec-7",
            title="2.2 — Sonja Anderson",
            content="Sonja holds a Bachelor of Microbiology from Federal University of Oye Ekiti.",
        )
        self.assertEqual(person_name_from_tab_title(section.title), "Sonja Anderson")
        self.assertTrue(is_personnel_bio_section(section))
        self.assertIn("Sonja Anderson", named_people_in_section(section))

    def test_org_structure_tab_is_not_a_person(self) -> None:
        self.assertEqual(
            person_name_from_tab_title("1.2 — Organizational Structure"),
            "",
        )

    def test_who_we_are_is_company_narrative_not_a_person(self) -> None:
        from app.models.proposal import ProposalSection
        from app.services.proposal_bio_stub import is_plausible_person_name
        from app.services.proposal_capability_bio_grounding import (
            is_named_person_bio_tab,
            is_who_we_are_section,
        )

        self.assertEqual(person_name_from_tab_title("1.1 — Who We Are"), "")
        self.assertEqual(person_name_from_tab_title("Who We Are"), "")
        self.assertFalse(is_plausible_person_name("Who We Are"))
        self.assertFalse(is_plausible_person_name("Our Work"))
        section = ProposalSection(
            id="section-1-who-we-are",
            title="1.1 — Who We Are",
            content="Role on this engagement: Team member on this engagement",
        )
        self.assertTrue(is_who_we_are_section(section))
        self.assertFalse(is_named_person_bio_tab(section))
        self.assertFalse(is_personnel_bio_section(section))

    def test_municipality_summaries_is_our_work_not_a_person(self) -> None:
        from app.models.proposal import ProposalSection
        from app.services.proposal_bio_stub import (
            is_plausible_person_name,
            prior_content_for_rewrite,
        )
        from app.services.proposal_capability_bio_grounding import (
            is_named_person_bio_tab,
        )

        self.assertEqual(
            person_name_from_tab_title("3.1 — Municipality Summaries"),
            "",
        )
        self.assertFalse(is_plausible_person_name("Municipality Summaries"))
        self.assertFalse(is_plausible_person_name("Municapility Summaries"))
        self.assertTrue(is_plausible_person_name("Sonja Anderson"))
        section = ProposalSection(
            id="section-3-work-01-municipality-summaries",
            title="3.1 — Municipality Summaries",
            content=(
                "### Municipality Summaries\n"
                "**Role on this engagement:** Team member on this engagement.\n\n"
                "[DESIGNER NOTE: Insert approved bio PDF — "
                "04_Bio_MunicipalitySummaries.pdf. Do not rewrite Key Accounts "
                "or work history in-manuscript.]\n"
            ),
        )
        self.assertFalse(is_named_person_bio_tab(section))
        self.assertFalse(is_personnel_bio_section(section))
        self.assertEqual(prior_content_for_rewrite(section.id, section.content or ""), "")

        from app.services.proposal_section_quality import prior_content_for_redraft

        prior, full_rewrite = prior_content_for_redraft(section)
        self.assertEqual(prior, "")
        self.assertTrue(full_rewrite)


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


class GroundBiosStubOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_section_2_bio_becomes_designer_note_without_llm(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

        draft = ProposalDraft(
            rfpId="rfp-bio",
            updatedAt="2026-08-14T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-2-bio-curt-schultz",
                    title="2.4 — Curt Schultz",
                    content=(
                        "### Curt Schultz\n"
                        "**Role on this engagement:** Team member on this engagement.\n\n"
                        "Curt Schultz has 30+ years of experience in graphic communications "
                        "and 18 years of agency ownership with a creative philosophy.\n"
                    ),
                )
            ],
        )
        updated, logs = await ground_bios_to_kb(draft, use_llm=True)
        body = updated.sections[0].content or ""
        self.assertTrue(logs)
        self.assertTrue(is_bio_pdf_designer_note(body))
        self.assertNotIn("30+ years", body)
        self.assertNotIn("agency ownership", body)
        self.assertIn("04_Bio_CurtSchultz.pdf", body)


    async def test_collapses_resume_dump_even_with_designer_note(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

        dump = (
            "### Ella Lindau\n"
            "**Role on this engagement:** Account and Operations Manager\n\n"
            "ACCOUNT AND OPERATIONS MANAGER | 5 YEARS WITH ZÖ AGENCY\n\n"
            "Ella Lindau oversees account operations and has led complex "
            "client programs across healthcare and government.\n\n"
            "[DESIGNER NOTE: Insert approved bio PDF — 04_Bio_EllaLindau.pdf. "
            "Do not rewrite Key Accounts or work history in-manuscript.]\n"
        )
        draft = ProposalDraft(
            rfpId="rfp-bio",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-2-bio-ella-lindau",
                    title="2.3 — Ella Lindau",
                    content=dump,
                )
            ],
        )
        updated, logs = await ground_bios_to_kb(draft, use_llm=True)
        body = updated.sections[0].content or ""
        self.assertTrue(logs)
        self.assertTrue(is_bio_pdf_designer_note(body))
        self.assertIn("Account and Operations Manager", body)
        self.assertNotIn("5 YEARS WITH ZÖ", body)
        self.assertNotIn("healthcare and government", body)


    async def test_persist_collapsed_bio_stubs_saves_without_llm(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services.proposal_capability_bio_grounding import persist_collapsed_bio_stubs

        dump = (
            "### Ella Lindau\n"
            "**Role on this engagement:** Account and Operations Manager\n\n"
            "ACCOUNT AND OPERATIONS MANAGER | 5 YEARS WITH ZÖ AGENCY\n"
            "Ella Lindau oversees account operations across healthcare.\n"
        )
        draft = ProposalDraft(
            rfpId="rfp-continue",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-2-bio-ella-lindau",
                    title="2.3 — Ella Lindau",
                    content=dump,
                )
            ],
        )
        saved: list[ProposalDraft] = []

        async def _save(d: ProposalDraft) -> None:
            saved.append(d)

        with patch(
            "app.services.proposal_repository.asave_proposal_draft",
            new=AsyncMock(side_effect=_save),
        ), patch(
            "app.services.llm.chat_json",
            new=AsyncMock(side_effect=AssertionError("no LLM for bio collapse")),
        ):
            updated, logs = await persist_collapsed_bio_stubs(
                "rfp-continue",
                draft=draft,
                rfp_text="Describe qualifications.",
            )
        self.assertTrue(logs)
        self.assertTrue(saved)
        body = (updated.sections[0].content if updated else "") or ""
        self.assertTrue(is_bio_pdf_designer_note(body))
        self.assertNotIn("5 YEARS WITH ZÖ", body)


    async def test_named_rfp_bio_tab_becomes_designer_note(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

        draft = ProposalDraft(
            rfpId="rfp-dh",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="rfp-sec-7",
                    title="2.2 — Sonja Anderson",
                    content=(
                        "Sonja holds a Bachelor of Microbiology from Federal "
                        "University of Oye Ekiti. She leads the agency."
                    ),
                )
            ],
        )
        updated, logs = await ground_bios_to_kb(
            draft,
            rfp_text="Bios must be included inline in the proposal body.",
            use_llm=True,
        )
        body = updated.sections[0].content or ""
        self.assertTrue(logs)
        self.assertTrue(is_bio_pdf_designer_note(body))
        self.assertNotIn("Oye Ekiti", body)
        self.assertIn("04_Bio_SonjaAnderson.pdf", body)

    async def test_ground_bios_keeps_who_we_are_prose(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

        prose = (
            "We are more than an agency — we are your strongest advocate and an "
            "extension of your team. zö means family, kindred, clan, community.\n\n"
            "## Our Promise\n\n"
            "Excellence is a guarantee, not a goal. We meet deadlines and budgets "
            "with full transparency."
        )
        draft = ProposalDraft(
            rfpId="rfp-who",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="1.1 — Who We Are",
                    content=prose,
                )
            ],
        )
        updated, logs = await ground_bios_to_kb(draft, rfp_text="Denver Health RFP")
        self.assertEqual(updated.sections[0].content, prose)
        self.assertFalse(is_bio_pdf_designer_note(updated.sections[0].content or ""))
        self.assertFalse(any("designer-note stub" in line for line in logs))

    async def test_ground_bios_does_not_stub_municipality_summaries_our_work(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_bio_stub import is_bio_pdf_designer_note
        from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

        prose = (
            "City of Umatilla and City of Medford campaigns — paid media, "
            "creative, and community outreach for municipal clients."
        )
        draft = ProposalDraft(
            rfpId="rfp-muni",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-3-work-01-municipality-summaries",
                    title="3.1 — Municipality Summaries",
                    content=prose,
                )
            ],
        )
        updated, logs = await ground_bios_to_kb(draft)
        self.assertEqual(updated.sections[0].content, prose)
        self.assertFalse(is_bio_pdf_designer_note(updated.sections[0].content or ""))
        self.assertFalse(any("designer-note stub" in line for line in logs))
        self.assertFalse(any("04_Bio_Municipality" in line for line in logs))

    async def test_ground_bios_does_not_touch_who_we_are(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_capability_bio_grounding import ground_bios_to_kb

        prose = (
            "We are more than an agency — we are your strongest advocate.\n\n"
            "## Our Promise\n\nExcellence is a guarantee, not a goal."
        )
        draft = ProposalDraft(
            rfpId="rfp-who",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-1-who-we-are",
                    title="1.1 — Who We Are",
                    content=prose,
                ),
                ProposalSection(
                    id="section-2-bio-sonja-anderson",
                    title="2.2 — Sonja Anderson",
                    content="Sonja invented a microbiology degree.",
                ),
            ],
        )
        updated, logs = await ground_bios_to_kb(draft)
        self.assertEqual(updated.sections[0].content, prose)
        self.assertTrue(any("04_Bio_SonjaAnderson.pdf" in line for line in logs))
        self.assertIn("04_Bio_SonjaAnderson.pdf", updated.sections[1].content or "")

    async def test_fetch_member_bio_kb_skips_who_we_are(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.services.proposal_sections_graph import _fetch_member_bio_kb

        with patch(
            "app.services.supermemory.search_document_chunks",
            new=AsyncMock(side_effect=AssertionError("no 04_Bio search for Who We Are")),
        ), patch(
            "app.services.supermemory.find_document_by_file_name",
            new=AsyncMock(side_effect=AssertionError("no 04_Bio lookup for Who We Are")),
        ):
            text, sources = await _fetch_member_bio_kb("Who We Are")
        self.assertEqual(text, "")
        self.assertEqual(sources, [])

    async def test_aligns_education_on_named_rfp_tab(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_capability_bio_grounding import align_named_bios_to_kb

        draft = ProposalDraft(
            rfpId="rfp-dh",
            updatedAt="2026-08-17T00:00:00Z",
            sections=[
                ProposalSection(
                    id="rfp-sec-7",
                    title="2.2 — Sonja Anderson",
                    content=(
                        "Sonja holds a Bachelor of Microbiology from Federal "
                        "University of Oye Ekiti. She leads the agency."
                    ),
                )
            ],
        )
        kb = (
            "Sonja Anderson, Agency Director.\nEducation:\n"
            "Associate of Arts, Grays Harbor College\n"
        )
        with patch(
            "app.services.proposal_sections_graph._fetch_member_bio_kb",
            new=AsyncMock(return_value=(kb, ["04_Bio_SonjaAnderson.pdf"])),
        ):
            updated, logs = await align_named_bios_to_kb(draft)
        body = updated.sections[0].content or ""
        self.assertTrue(logs)
        self.assertNotIn("Oye Ekiti", body)
        self.assertIn("Grays Harbor", body)


if __name__ == "__main__":
    unittest.main()
