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
    renumber_dynamic_group_titles,
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

    def test_heuristic_instead_of_bio_replace(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_bio_replace_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-brian", "2.2 — Brian Niles", "### Brian\n"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = _heuristic_bio_replace_plan(
            "Instead of Brian Niles bio add Ron Comer bio",
            draft,
            focus_section_id="section-2-bio-brian",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.action, "add_sections")
        self.assertEqual(plan.deletions[0].section_id, "section-2-bio-brian")
        self.assertEqual(plan.additions[0].member_name, "Ron Comer")

    def test_heuristic_replace_case_study(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_section_replace_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-3-work-deschutes", "3.1 — Deschutes Brewery", "case"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = _heuristic_section_replace_plan(
            "Instead of Deschutes Brewery add Hampton Lumber",
            draft,
            focus_section_id="section-3-work-deschutes",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.additions[0].kind, "case_study")
        self.assertEqual(plan.additions[0].case_study_name, "Hampton Lumber")
        self.assertEqual(plan.deletions[0].section_id, "section-3-work-deschutes")

    def test_heuristic_get_ron_bio_on_brian_tab(self) -> None:
        from app.services.proposal_chat_structure import _heuristic_bio_replace_plan

        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                _sec("section-2-bio-brian", "2.2 — Brian Niles", "### Brian\n"),
            ],
            updatedAt="2026-07-22T00:00:00+00:00",
        )
        plan = _heuristic_bio_replace_plan(
            "see section 2.2 and fill all verify tags and get ron comer bio there",
            draft,
            focus_section_id="section-2-bio-brian",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.additions[0].member_name, "Ron Comer")
        self.assertEqual(plan.deletions[0].section_id, "section-2-bio-brian")


if __name__ == "__main__":
    unittest.main()
