"""Tests for chat-driven add/delete section structure helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_chat_structure import (
    StructureAddition,
    StructureDeletion,
    StructurePlan,
    _is_placeholder_member_name,
    _pick_roster_members,
    apply_chat_structure_plan,
    infer_case_study_name_from_content,
    renumber_dynamic_group_titles,
    sync_case_study_title_from_content,
)


def _sec(sid: str, title: str, content: str = "x") -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        source="template",
        mode="select",
    )


class ChatStructureTests(unittest.IsolatedAsyncioTestCase):
    def test_renumber_bios_and_case_studies(self) -> None:
        sections = [
            _sec("section-1-who", "1.1 — Who We Are"),
            _sec("section-2-bio-sonja", "2.1 — Sonja Anderson"),
            _sec("section-2-bio-todd", "2.1 — Todd Anderson"),
            _sec("section-3-work-a", "3.1 — Acme"),
            _sec("section-3-work-b", "3.9 — Beta"),
        ]
        out = renumber_dynamic_group_titles(sections)
        titles = [s.title for s in out]
        self.assertEqual(titles[1], "2.1 — Sonja Anderson")
        self.assertEqual(titles[2], "2.2 — Todd Anderson")
        self.assertEqual(titles[3], "3.1 — Acme")
        self.assertEqual(titles[4], "3.2 — Beta")

    def test_sync_case_study_title_when_body_swaps_client(self) -> None:
        section = _sec(
            "section-3-work-04-03_cs_city-of-umatilla_digital-campaign_",
            "3.4 — City of Umatilla Digital Campaign 2006",
            content=(
                "CITY OF SAN LEANDRO: CITY BRAND ASSESSMENT AND MARKETING PLAN\n\n"
                "### Client overview\n"
                "The City of San Leandro, California needed a brand assessment.\n"
            ),
        )
        synced = sync_case_study_title_from_content(section)
        self.assertTrue(synced.title.startswith("3.4 — "))
        self.assertIn("San Leandro", synced.title)
        self.assertNotIn("Umatilla", synced.title)

    def test_infer_case_study_name_from_heading(self) -> None:
        name = infer_case_study_name_from_content(
            "## City of San Leandro: Brand Assessment\n\nWe partnered with San Leandro."
        )
        self.assertIsNotNone(name)
        assert name is not None
        self.assertIn("San Leandro", name)

    def test_sync_case_study_cross_references_updates_stale_pointers(self) -> None:
        from app.services.proposal_chat_structure import sync_case_study_cross_references

        old_work = _sec(
            "section-3-work-01",
            "3.1 — City of Umatilla Digital Campaign 2006",
            "Umatilla body",
        )
        new_work = old_work.model_copy(
            update={
                "title": "3.1 — San Francisco Travel: Summer of Love",
                "content": "SF Travel body",
            }
        )
        tourism = _sec(
            "rfp-tourism",
            "Examples of Tourism Social Media",
            (
                "CITY OF UMATILLA ROCK THE LOCK MUSIC FESTIVAL\n\n"
                "See 3.1, City of Umatilla Rock the Lock Music Festival in Our Work "
                "for the full case narrative.\n"
            ),
        )
        sections, n = sync_case_study_cross_references(
            [new_work, tourism],
            changed=new_work,
            old_title=old_work.title,
        )
        self.assertEqual(n, 1)
        updated = next(s for s in sections if s.id == "rfp-tourism")
        self.assertIn("San Francisco Travel", updated.content or "")
        self.assertNotIn("Umatilla Rock the Lock", updated.content or "")

    def test_placeholder_detection(self) -> None:
        self.assertTrue(_is_placeholder_member_name(None))
        self.assertTrue(_is_placeholder_member_name("[VERIFY: team member 2]"))
        self.assertFalse(_is_placeholder_member_name("Todd Anderson"))

    def test_pick_roster_skips_existing(self) -> None:
        profiles = [
            {"name": "Sonja Anderson", "title": "Director"},
            {"name": "Todd Anderson", "title": "Principal"},
            {"name": "Alex Intern", "title": "Intern"},
            {"name": "Jamie Developer", "title": "Developer"},
        ]
        picked = _pick_roster_members(
            profiles, exclude={"sonja anderson"}, count=2
        )
        self.assertEqual(picked, ["Todd Anderson", "Jamie Developer"])

    async def test_add_custom_section(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[_sec("section-1-who", "1.1 — Who We Are")],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = StructurePlan(
            action="add_sections",
            additions=[
                StructureAddition(
                    title="Accessibility Approach",
                    kind="custom",
                    draftHint="Cover WCAG 2.2 AA.",
                )
            ],
            assistantNote="Adding accessibility tab.",
        )
        updated, focus, message = await apply_chat_structure_plan(
            draft=draft, plan=plan, rfp_client="GSP"
        )
        self.assertEqual(len(updated.sections), 2)
        self.assertEqual(focus.title, "Accessibility Approach")
        self.assertIn("Added section", message)

    async def test_delete_section(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-sonja", "2.1 — Sonja Anderson"),
                _sec("section-2-bio-todd", "2.2 — Todd Anderson"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = StructurePlan(
            action="delete_sections",
            deletions=[StructureDeletion(sectionId="section-2-bio-todd")],
        )
        updated, focus, message = await apply_chat_structure_plan(
            draft=draft, plan=plan, rfp_client="GSP"
        )
        self.assertEqual(len(updated.sections), 1)
        self.assertEqual(updated.sections[0].title, "2.1 — Sonja Anderson")
        self.assertIn("Deleted", message)

    async def test_add_bios_resolves_placeholders_from_roster(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec(
                    "section-2-bio-sonja",
                    "2.1 — Sonja Anderson",
                    "### Sonja Anderson\n",
                ),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = StructurePlan(
            action="add_sections",
            additions=[
                StructureAddition(
                    title="2.2 — [VERIFY: team member 3]",
                    kind="bio",
                    memberName=None,
                ),
                StructureAddition(
                    title="2.3 — [VERIFY: team member 2]",
                    kind="bio",
                    memberName="[VERIFY: team member 2]",
                ),
            ],
        )

        fake_profiles = [
            {"name": "Sonja Anderson", "title": "Director"},
            {"name": "Todd Anderson", "title": "Principal"},
            {"name": "Jamie Developer", "title": "Developer"},
        ]

        async def fake_build(*, member_name: str, index: int, rfp_client: str):
            return ProposalSection(
                id=f"section-2-bio-{member_name.split()[0].casefold()}",
                title=f"2.{index} — {member_name}",
                content=f"### {member_name}\n\nFull drafted bio.",
                source="template",
                mode="select",
                status="generated",
                wordTarget=500,
                required=True,
            )

        with (
            patch(
                "app.services.proposal_knowledge_base_tools.fetch_master_team_roster",
                new=AsyncMock(return_value=("roster text", [])),
            ),
            patch(
                "app.services.company_qualification.agents.team_selection.build_roster_profiles",
                return_value=fake_profiles,
            ),
            patch(
                "app.services.proposal_chat_structure._build_bio_section",
                new=fake_build,
            ),
        ):
            updated, focus, message = await apply_chat_structure_plan(
                draft=draft,
                plan=plan,
                rfp_client="GSP",
                rfp_context="airport marketing RFP",
            )

        bio_titles = [
            s.title for s in updated.sections if s.id.startswith("section-2-bio")
        ]
        self.assertEqual(
            bio_titles,
            ["2.1 — Sonja Anderson", "2.2 — Todd Anderson", "2.3 — Jamie Developer"],
        )
        self.assertNotIn("VERIFY", message)
        self.assertIn("Todd Anderson", message)
        self.assertIn("Jamie Developer", message)
        self.assertIn("Full drafted bio", updated.sections[1].content)


    async def test_add_bios_fills_verify_stubs_in_place(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec(
                    "section-2-bio-sonja",
                    "2.1 — Sonja Anderson",
                    "### Sonja Anderson\n",
                ),
                _sec(
                    "section-2-bio-verify-3",
                    "2.2 — [VERIFY: team member 3]",
                    "[VERIFY: Bio for [VERIFY: team member 3]]",
                ),
                _sec(
                    "section-2-bio-verify-2",
                    "2.3 — [VERIFY: team member 2]",
                    "[VERIFY: Bio for [VERIFY: team member 2]]",
                ),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = StructurePlan(
            action="add_sections",
            additions=[
                StructureAddition(kind="bio", memberName=None),
                StructureAddition(kind="bio", memberName=None),
            ],
        )
        fake_profiles = [
            {"name": "Sonja Anderson", "title": "Director"},
            {"name": "Todd Anderson", "title": "Principal"},
            {"name": "Jamie Developer", "title": "Developer"},
        ]

        async def fake_build(*, member_name: str, index: int, rfp_client: str):
            return ProposalSection(
                id=f"section-2-bio-{member_name.split()[0].casefold()}",
                title=f"2.{index} — {member_name}",
                content=f"### {member_name}\n\nFull drafted bio.",
                source="template",
                mode="select",
                status="generated",
                wordTarget=500,
                required=True,
            )

        with (
            patch(
                "app.services.proposal_knowledge_base_tools.fetch_master_team_roster",
                new=AsyncMock(return_value=("roster text", [])),
            ),
            patch(
                "app.services.company_qualification.agents.team_selection.build_roster_profiles",
                return_value=fake_profiles,
            ),
            patch(
                "app.services.proposal_chat_structure._build_bio_section",
                new=fake_build,
            ),
        ):
            updated, focus, message = await apply_chat_structure_plan(
                draft=draft,
                plan=plan,
                rfp_client="GSP",
                rfp_context="airport marketing RFP",
            )

        bio_titles = [
            s.title for s in updated.sections if s.id.startswith("section-2-bio")
        ]
        self.assertEqual(len(bio_titles), 3)
        self.assertEqual(
            bio_titles,
            ["2.1 — Sonja Anderson", "2.2 — Todd Anderson", "2.3 — Jamie Developer"],
        )
        self.assertNotIn("VERIFY", "".join(bio_titles))
        self.assertIn("Filled bio", message)


    async def test_replace_bio_delete_then_add(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-sonja", "2.1 — Sonja Anderson", "### Sonja\n"),
                _sec("section-2-bio-brian", "2.2 — Brian Niles", "### Brian\n"),
                _sec("section-2-bio-rachel", "2.3 — Rachel Rice", "### Rachel\n"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = StructurePlan(
            action="add_sections",
            deletions=[StructureDeletion(sectionId="section-2-bio-brian")],
            additions=[
                StructureAddition(
                    kind="bio",
                    memberName="Ron Comer",
                    title="2.2 — Ron Comer",
                )
            ],
            assistantNote="Replacing Brian with Ron.",
        )

        async def fake_build(*, member_name: str, index: int, rfp_client: str):
            return ProposalSection(
                id=f"section-2-bio-{member_name.split()[0].casefold()}",
                title=f"2.{index} — {member_name}",
                content=f"### {member_name}\n\nFull drafted bio.",
                source="template",
                mode="select",
                status="generated",
                wordTarget=500,
                required=True,
            )

        with patch(
            "app.services.proposal_chat_structure._build_bio_section",
            new=fake_build,
        ):
            updated, focus, message = await apply_chat_structure_plan(
                draft=draft, plan=plan, rfp_client="GSP"
            )

        titles = [s.title for s in updated.sections]
        self.assertEqual(
            titles,
            ["2.1 — Sonja Anderson", "2.2 — Ron Comer", "2.3 — Rachel Rice"],
        )
        self.assertIn("Ron Comer", message)
        self.assertIn("Replaced", message)
        self.assertNotIn("Brian Niles", " ".join(titles))
        self.assertEqual(focus.title, "2.2 — Ron Comer")
        self.assertIn("Full drafted bio", focus.content)

    def test_regex_replace_heuristic_disabled_for_prose_and_bios(self) -> None:
        """Structure swaps must be LLM-planned — regex must not rename tabs."""
        from app.services.proposal_chat_structure import (
            _heuristic_bio_replace_plan,
            _heuristic_section_replace_plan,
        )

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-brian", "2.2 — Brian Niles", "### Brian\n"),
                _sec("rfp-sec-9", "Project Staffing Plan", "Ron allocates 25%."),
                _sec("section-3-work-deschutes", "3.1 — Deschutes Brewery", "case"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        prose = (
            "The 25% and 60% figures in this section are also unsourced — "
            "remove them or replace with qualitative language."
        )
        self.assertIsNone(
            _heuristic_section_replace_plan(
                prose, draft, focus_section_id="rfp-sec-9"
            )
        )
        self.assertIsNone(
            _heuristic_bio_replace_plan(
                "Instead of Brian Niles bio add Ron Comer bio",
                draft,
                focus_section_id="section-2-bio-brian",
            )
        )
        self.assertIsNone(
            _heuristic_section_replace_plan(
                "Instead of Deschutes Brewery add Hampton Lumber",
                draft,
                focus_section_id="section-3-work-deschutes",
            )
        )

    def test_fill_verify_from_kb_only_does_not_create_kb_only_tab(self) -> None:
        from app.services.proposal_chat_structure import (
            _heuristic_section_replace_plan,
            _is_in_place_kb_or_verify_edit,
        )

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec(
                    "rfp-form-3-references",
                    "Form 3 — References",
                    "[VERIFY: contact name]\n[VERIFY: phone]",
                ),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        message = "Fill [VERIFY] tags from KB only."
        self.assertTrue(_is_in_place_kb_or_verify_edit(message))
        plan = _heuristic_section_replace_plan(
            message,
            draft,
            focus_section_id="rfp-form-3-references",
        )
        self.assertIsNone(plan)


class AddCaseStudyHeuristicTests(unittest.TestCase):
    def test_add_rno_in_case_studies_does_not_replace_previous_experience(self) -> None:
        from app.services.proposal_chat_structure import (
            _heuristic_add_case_study_plan,
            _heuristic_section_replace_plan,
        )

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-1-who", "1.1 — Who We Are"),
                _sec("section-3-work-deschutes", "3.1 — Deschutes Brewery", "case"),
                _sec("rfp-prev", "Previous Experience", "old"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        message = (
            "Recovery Network of Oregon is still nowhere in this document. "
            "add this section in Case studies."
        )
        replace = _heuristic_section_replace_plan(
            message, draft, focus_section_id="section-1-who"
        )
        self.assertIsNone(replace)
        plan = _heuristic_add_case_study_plan(message, draft)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "add_sections")
        self.assertEqual(plan.deletions, [])
        self.assertEqual(plan.additions[0].kind, "case_study")
        self.assertEqual(
            plan.additions[0].case_study_name, "Recovery Network of Oregon"
        )

    def test_add_rno_when_already_present_opens_edit(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_add_case_study_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec(
                    "section-3-work-rno",
                    "3.2 — Recovery Network of Oregon",
                    "RNO coalition work",
                ),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = _heuristic_add_case_study_plan(
            "add Recovery Network of Oregon in case studies",
            draft,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "edit")
        self.assertEqual(plan.edit_section_id, "section-3-work-rno")


class AddBioHeuristicTests(unittest.TestCase):
    def test_add_ron_comer_alongside_creates_new_bio_tab(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_add_bio_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-letitia", "2.3 — Letitia Hopper", "Letitia bio"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        message = (
            "Add a new bio section for Ron Comer alongside the existing "
            "Letitia Hopper section."
        )
        plan = _heuristic_add_bio_plan(message, draft)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "add_sections")
        self.assertEqual(plan.deletions, [])
        self.assertEqual(plan.additions[0].kind, "bio")
        self.assertEqual(plan.additions[0].member_name, "Ron Comer")
        self.assertEqual(plan.additions[0].insert_after_section_id, "section-2-bio-letitia")

    def test_add_bio_when_already_present_opens_edit(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_add_bio_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-ron", "2.2 — Ron Comer", "Ron bio"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = _heuristic_add_bio_plan(
            "Add Ron Comer bio alongside the team",
            draft,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "edit")
        self.assertEqual(plan.edit_section_id, "section-2-bio-ron")


class AddGenericSectionTests(unittest.IsolatedAsyncioTestCase):
    def test_is_add_section_intent_for_custom_tab(self) -> None:
        from app.services.proposal_chat_structure import is_add_section_intent

        self.assertTrue(
            is_add_section_intent(
                "Add a new section titled Project Timeline alongside the existing section."
            )
        )
        self.assertFalse(is_add_section_intent("Fill [VERIFY] tags from KB only."))
        self.assertFalse(
            is_add_section_intent("here add client voice for this section")
        )
        self.assertTrue(
            is_add_section_intent("add another team bio for the roster")
        )

    def test_custom_title_not_rejected_when_member_case_null(self) -> None:
        """Regression: null memberName/caseStudyName used to trip _is_bogus_structure_title(None)."""
        from app.services.proposal_chat_structure import _is_bogus_structure_title

        self.assertFalse(_is_bogus_structure_title("Planning and Methodology"))
        self.assertTrue(_is_bogus_structure_title(None))
        self.assertTrue(_is_bogus_structure_title(""))

    async def test_plan_keeps_custom_add_with_null_member_case_names(self) -> None:
        """Island County bug: LLM returned add_sections + title, then guard dropped it."""
        from app.services.proposal_chat_structure import plan_chat_structure_action

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[_sec("section-1-who-we-are", "1.1 — Who We Are", "who we are")],
            updatedAt="2026-08-10T00:00:00+00:00",
        )
        llm_plan = StructurePlan(
            action="add_sections",
            additions=[
                StructureAddition(
                    kind="custom",
                    title="Planning and Methodology",
                    insertAfterSectionId="section-1-who-we-are",
                    draftHint="Draft planning and methodology for this RFP.",
                )
            ],
            assistantNote="Adding Planning and Methodology as a new sidebar tab.",
        )
        with patch(
            "app.services.proposal_chat_structure._structure_plan_llm_once",
            new=AsyncMock(return_value=llm_plan),
        ):
            plan = await plan_chat_structure_action(
                draft=draft,
                user_message="Add section new name Planning and methodology",
                focus_section_id="section-1-who-we-are",
                rfp_title="Island County Tourism",
                rfp_client="Island County",
                rfp_context="",
                chat_intent="structure",
            )
        self.assertEqual(plan.action, "add_sections")
        self.assertEqual(len(plan.additions), 1)
        self.assertEqual(plan.additions[0].title, "Planning and Methodology")

    def test_generic_add_creates_new_custom_tab(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_add_generic_section_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("custom-compliance", "Submission Compliance", "compliance prose"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        message = (
            "Add a new section titled Project Timeline alongside the existing "
            "Submission Compliance section."
        )
        plan = _heuristic_add_generic_section_plan(
            message, draft, focus_section_id="custom-compliance"
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "add_sections")
        self.assertEqual(plan.additions[0].kind, "custom")
        self.assertEqual(plan.additions[0].title, "Project Timeline")
        self.assertEqual(plan.additions[0].insert_after_section_id, "custom-compliance")

    def test_coerce_edit_plan_to_add_sections(self) -> None:
        from app.services.proposal_chat_structure import (
            StructurePlan,
            _coerce_add_section_plan,
        )

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[_sec("rfp-timeline", "Implementation Timeline", "timeline")],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        wrong = StructurePlan(action="edit", editSectionId="rfp-timeline")
        message = "Create a new section titled Staffing Plan alongside Implementation Timeline."
        coerced = _coerce_add_section_plan(
            wrong, message, draft, focus_section_id="rfp-timeline"
        )
        self.assertEqual(coerced.action, "add_sections")
        self.assertEqual(coerced.additions[0].title, "Staffing Plan")
        self.assertEqual(coerced.deletions, [])

    def test_section_name_phrasing_extracts_title(self) -> None:
        from app.services.proposal_chat_structure import (
            _extract_generic_section_title_from_add_message,
            _heuristic_add_generic_section_plan,
        )

        for msg in (
            "add new section name Planning and methodology",
            "Add section new name Planning and methodology",
        ):
            with self.subTest(msg=msg):
                self.assertEqual(
                    _extract_generic_section_title_from_add_message(msg),
                    "Planning and methodology",
                )
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[_sec("s1", "1.1 — Who We Are")],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = _heuristic_add_generic_section_plan(
            "Add section new name Planning and methodology",
            draft,
            focus_section_id="s1",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "add_sections")
        self.assertEqual(plan.additions[0].title, "Planning and methodology")


class AttestationInPlaceTests(unittest.TestCase):
    def test_here_fill_investment_is_in_place_on_open_case_study(self) -> None:
        from app.services.proposal_chat_structure import _is_in_place_section_budget_fill

        section = ProposalSection(
            id="cs-oregon",
            title="3.3 — Oregon Employment",
            content=(
                "| Phase | Scope | Investment |\n"
                "| Discovery | Audit | $[VERIFY: budget figure] |\n"
            ),
            status="generated",
        )
        self.assertTrue(
            _is_in_place_section_budget_fill("here fill Investment part!", section)
        )
        self.assertFalse(
            _is_in_place_section_budget_fill(
                "add a new Investment sidebar tab", section
            )
        )

    def test_everify_ask_is_in_place_not_structure(self) -> None:
        from app.services.proposal_chat_structure import (
            _is_bogus_structure_title,
            _is_in_place_kb_or_verify_edit,
            _heuristic_section_replace_plan,
        )

        msg = (
            "E-Verify must not be asserted — Sonja or Ella must confirm. "
            "Use HUMAN SIGN-OFF / VERIFY, do not invent compliance."
        )
        self.assertTrue(_is_in_place_kb_or_verify_edit(msg))
        self.assertTrue(
            _is_bogus_structure_title(
                "placeholder: '[HUMAN SIGN-OFF REQUIRED: E-Verify enrollment…]'"
            )
        )
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("rfp-everify", "Contractor Affidavit (E-Verify)", "we maintain E-Verify"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        self.assertIsNone(
            _heuristic_section_replace_plan(
                msg, draft, focus_section_id="rfp-everify"
            )
        )


class SplitSectionTests(unittest.IsolatedAsyncioTestCase):
    def test_sanitize_long_instruction_title(self) -> None:
        from app.services.proposal_chat_structure import _sanitize_addition_title

        title = _sanitize_addition_title(
            "Create a new H2 section titled Project Staff Planning "
            "(separate from Evaluation Metrics); move that staff content"
        )
        self.assertEqual(title, "Project Staff Planning")
        self.assertFalse(len(title) > 60)

    def test_long_title_is_not_bogus_just_for_length(self) -> None:
        from app.services.proposal_chat_structure import _is_bogus_structure_title

        # Length alone must not reject a real section name (coerce used to).
        self.assertFalse(
            _is_bogus_structure_title(
                "Project Staff Planning and Continuity Assurance Overview Extra"
            )
        )

    async def test_split_move_applies_extracted_content(self) -> None:
        from app.services.proposal_chat_structure import (
            StructureAddition,
            StructurePlan,
            apply_chat_structure_plan,
        )

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec(
                    "rfp-sec-8",
                    "Evaluation Metrics",
                    "## Evaluation Metrics\n\nScorecards here.\n\n"
                    "## Project Staff Planning\n\nRon leads staffing.\n\n"
                    "Team allocation details.",
                ),
            ],
            updatedAt="2026-07-23T00:00:00+00:00",
        )
        plan = StructurePlan(
            action="add_sections",
            additions=[
                StructureAddition(
                    title="Project Staff Planning",
                    kind="rfp",
                    insertAfterSectionId="rfp-sec-8",
                    extractFromSectionId="rfp-sec-8",
                    removeExtractedFromSource=True,
                    draftHint="Move Project Staff Planning out of Evaluation Metrics",
                )
            ],
            assistantNote="Splitting Project Staff Planning into its own section.",
        )

        async def _fake_split(**kwargs):
            return (
                "## Project Staff Planning\n\nRon leads staffing.\n\nTeam allocation details.",
                "## Evaluation Metrics\n\nScorecards here.",
            )

        with patch(
            "app.services.proposal_chat_structure._split_section_content",
            new=AsyncMock(side_effect=_fake_split),
        ):
            updated, focus, message = await apply_chat_structure_plan(
                draft=draft, plan=plan, rfp_client="GSU"
            )

        self.assertEqual(focus.title, "Project Staff Planning")
        self.assertIn("Ron leads staffing", focus.content or "")
        source = next(s for s in updated.sections if s.id == "rfp-sec-8")
        self.assertIn("Scorecards", source.content or "")
        self.assertNotIn("Ron leads staffing", source.content or "")
        self.assertEqual(len(updated.sections), 2)
        self.assertIn("Moved", message)

    def test_in_place_kb_fetch_on_named_case_study_in_open_tab(self) -> None:
        from app.services.proposal_chat_structure import _is_in_place_kb_or_verify_edit

        msg = "here San Francisco Travel case study is empty fetch that"
        self.assertTrue(_is_in_place_kb_or_verify_edit(msg))

    def test_replace_case_study_still_not_in_place(self) -> None:
        from app.services.proposal_chat_structure import _is_in_place_kb_or_verify_edit

        msg = "replace Hampton Lumber case study with Bend from KB"
        self.assertFalse(_is_in_place_kb_or_verify_edit(msg))


if __name__ == "__main__":
    unittest.main()
