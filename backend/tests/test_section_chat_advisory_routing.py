"""Tests for advisory vs rewrite routing in section chat."""

from __future__ import annotations

import sys
import types
import unittest

if "langchain_openai" not in sys.modules:
    stub = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # noqa: D401
        pass

    stub.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = stub

from app.services.proposal_section_editor import _wants_section_edit


class WantsSectionEditTests(unittest.TestCase):
    def test_case_study_fit_audit_is_advisory(self) -> None:
        msg = (
            "now check all case studies meet expetations of rfp "
            "if not then list which dont"
        )
        self.assertFalse(_wants_section_edit(msg))

    def test_evaluate_questions_are_advisory(self) -> None:
        self.assertFalse(_wants_section_edit("Does 3.3 meet the RFP?"))
        self.assertFalse(_wants_section_edit("which case studies don't fit"))
        self.assertFalse(
            _wants_section_edit("review all Our Work sections against requirements")
        )

    def test_explicit_rewrites_still_edit(self) -> None:
        self.assertTrue(_wants_section_edit("rewrite 3.3 with San Leandro from KB"))
        self.assertTrue(_wants_section_edit("replace Maricopa with a tourism case study"))
        self.assertTrue(_wants_section_edit("improve this section — more punchy"))
        self.assertTrue(_wants_section_edit("add a paragraph about seasonal campaigns"))
        self.assertTrue(_wants_section_edit("add a new section titled Project Staff Planning"))
        self.assertTrue(_wants_section_edit("create a new sidebar section for references"))
        self.assertTrue(_wants_section_edit("add another team bio"))

    def test_in_place_client_voice_add_is_edit(self) -> None:
        self.assertTrue(_wants_section_edit("here add client voice in this"))
        self.assertTrue(_wants_section_edit("here add client voice for this section"))

    def test_skip_structure_planner_for_scoped_content_add(self) -> None:
        from app.services.proposal_section_editor import _should_skip_structure_planner

        self.assertTrue(
            _should_skip_structure_planner(
                "single_edit",
                user_message="here add client voice in this",
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=True,
            )
        )
        self.assertTrue(
            _should_skip_structure_planner(
                "single_edit",
                user_message="here add client voice in this",
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=False,
            )
        )

    def test_whole_new_section_runs_structure_planner_not_open_tab_edit(self) -> None:
        from app.services.proposal_section_editor import _should_skip_structure_planner

        for msg in (
            "add a new section titled Project Staff Planning",
            "add one whole new section for accessibility",
            "create a new sidebar section for references",
        ):
            with self.subTest(msg=msg):
                self.assertFalse(
                    _should_skip_structure_planner(
                        "structure",
                        user_message=msg,
                        selection_mode=False,
                        apply_fix=False,
                        improve_section_pinned=True,
                    )
                )

    def test_improve_pin_skips_structure_for_content_edit(self) -> None:
        from app.services.proposal_section_editor import _should_skip_structure_planner

        self.assertTrue(
            _should_skip_structure_planner(
                "single_edit",
                user_message=(
                    "make it short and concise not very long and use tables "
                    "and all even add designer note if needed"
                ),
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=True,
            )
        )
        self.assertTrue(
            _should_skip_structure_planner(
                "single_edit",
                user_message="make it short and concise",
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=False,
            )
        )

    def test_bio_designer_note_ask_runs_structure_planner(self) -> None:
        from app.services.proposal_section_editor import _should_skip_structure_planner

        msg = (
            "here remove this whole bio and add Designer note "
            "of attachment of this resume"
        )
        self.assertFalse(
            _should_skip_structure_planner(
                "single_edit",
                user_message=msg,
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=True,
            )
        )
        self.assertFalse(
            _should_skip_structure_planner(
                "structure",
                user_message=msg,
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=False,
            )
        )

    def test_improve_pin_does_not_skip_add_section_structure(self) -> None:
        from app.services.proposal_section_editor import _should_skip_structure_planner

        self.assertFalse(
            _should_skip_structure_planner(
                "single_edit",
                user_message="Add section new name Planning and methodology",
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=True,
            )
        )

    def test_understood_ask_implies_sidebar_add(self) -> None:
        from app.services.proposal_section_editor import _understood_ask_implies_sidebar_add

        self.assertTrue(
            _understood_ask_implies_sidebar_add(
                "User wants to add a new sidebar section titled 'Planning and methodology'"
            )
        )
        self.assertFalse(
            _understood_ask_implies_sidebar_add("Improve punchy opening paragraph")
        )
        from app.services.proposal_section_editor import _should_skip_structure_planner

        self.assertFalse(
            _should_skip_structure_planner(
                "single_edit",
                user_message="add section new name Planning and methodology",
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=False,
            )
        )

    def test_structure_intent_routes_edit(self) -> None:
        from app.services.proposal_section_editor import decide_chat_route

        route = decide_chat_route(
            chat_intent="structure",
            user_message="add section new name Planning and methodology",
            selection_mode=False,
        )
        self.assertFalse(route.advisory)
        self.assertEqual(route.reason, "classifier_structure")
        from app.services.proposal_section_editor import _should_skip_structure_planner

        self.assertFalse(
            _should_skip_structure_planner(
                "structure",
                user_message="add section new name Planning and methodology",
                selection_mode=False,
                apply_fix=False,
                improve_section_pinned=True,
            )
        )

    def test_finish_structure_clarify_ignored_for_content_edits(self) -> None:
        import asyncio
        from datetime import datetime, timezone

        from app.models.proposal import ProposalDraft, ProposalSection
        from app.models.rfp import RfpRecord
        from app.services.proposal_chat_structure import StructurePlan
        from app.services.proposal_section_editor import _finish_chat_structure_plan

        now = datetime.now(timezone.utc).isoformat()
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                ProposalSection(
                    id="rfp-planning-and-methodology",
                    title="Planning and Methodology",
                    content="long prose",
                    source="rfp",
                    mode="select",
                )
            ],
            updatedAt=now,
        )
        rfp = RfpRecord(
            id="rfp-1",
            title="Island County",
            client="Island County",
            sector="Public Sector",
            dueDate=now,
            receivedDate=now,
            lastActivity=now,
            lastActivityNote="test",
        )
        plan = StructurePlan(
            action="clarify",
            clarifyQuestion="Should I edit or add sections?",
        )

        async def _run() -> None:
            blocked = await _finish_chat_structure_plan(
                rfp_id="rfp-1",
                draft=draft,
                structure_plan=plan,
                section_id="rfp-planning-and-methodology",
                rfp=rfp,
                rfp_context="",
                research=None,
                persist=False,
                allow_clarify=False,
            )
            self.assertIsNone(blocked)
            allowed = await _finish_chat_structure_plan(
                rfp_id="rfp-1",
                draft=draft,
                structure_plan=plan,
                section_id="rfp-planning-and-methodology",
                rfp=rfp,
                rfp_context="",
                research=None,
                persist=False,
                allow_clarify=True,
            )
            self.assertIsNotNone(allowed)
            assert allowed is not None
            self.assertFalse(allowed[5])  # changed
            self.assertIn("edit", (allowed[4] or "").lower())

        asyncio.run(_run())

    def test_case_study_clarify_guards(self) -> None:
        from app.services.proposal_section_editor import _message_needs_case_study_clarify

        self.assertFalse(
            _message_needs_case_study_clarify(
                "add a new case study from the knowledge base"
            )
        )
        self.assertTrue(
            _message_needs_case_study_clarify(
                "improve these existing case studies for the RFP"
            )
        )

    def test_verify_fill_with_case_study_wording_is_not_clarify(self) -> None:
        from app.services.proposal_section_editor import (
            _message_needs_case_study_clarify,
            _open_section_owns_case_study_ask,
            _selection_asks_to_fill_verify,
        )
        from app.models.proposal import ProposalSection

        msg = (
            '[VERIFY: Request San Francisco Travel case study details from Sonja, '
            "specifically: account management duration, platforms managed] "
            "fill this verify tag from knowledge base"
        )
        self.assertTrue(_selection_asks_to_fill_verify(msg))
        self.assertFalse(_message_needs_case_study_clarify(msg))

        open_tab = ProposalSection(
            id="sec-tourism-examples",
            title="Examples of Tourism or Destination Marketing Social Media Accounts Managed",
            content=(
                "## San Francisco Travel\n"
                "[VERIFY: Request San Francisco Travel case study details from Sonja, "
                "specifically: account management duration, platforms managed]\n"
            ),
        )
        self.assertTrue(_open_section_owns_case_study_ask(msg, open_tab))

    def test_verify_fill_routes_edit_even_if_classifier_says_advisory(self) -> None:
        from app.services.proposal_section_editor import decide_chat_route

        msg = (
            "[VERIFY: Request San Francisco Travel case study details from Sonja] "
            "fill this verify tag from knowledge base"
        )
        route = decide_chat_route(
            chat_intent="advisory",
            user_message=msg,
            selection_mode=False,
        )
        self.assertFalse(route.advisory)
        self.assertEqual(route.reason, "kb_fetch_or_verify_fill")

    def test_empty_fetch_that_routes_edit_not_advisory(self) -> None:
        from app.services.proposal_section_editor import (
            _message_needs_case_study_clarify,
            decide_chat_route,
        )
        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

        msg = "here San Francisco Travel case study is empty fetch that"
        self.assertTrue(user_asks_kb_fetch_or_fill(msg))
        self.assertFalse(_message_needs_case_study_clarify(msg))
        route = decide_chat_route(
            chat_intent="advisory",
            user_message=msg,
            selection_mode=False,
        )
        self.assertFalse(route.advisory)
        self.assertEqual(route.reason, "kb_fetch_or_verify_fill")

    def test_fill_from_kb_routes_edit(self) -> None:
        from app.services.proposal_section_editor import decide_chat_route
        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

        msg = "fill San Francisco Travel from KB"
        self.assertTrue(user_asks_kb_fetch_or_fill(msg))
        route = decide_chat_route(
            chat_intent="advisory",
            user_message=msg,
            selection_mode=False,
        )
        self.assertFalse(route.advisory)

    def test_kb_fetch_is_wants_section_edit(self) -> None:
        msg = "here San Francisco Travel case study is empty fetch thst"
        self.assertTrue(_wants_section_edit(msg))

    def test_general_kb_fetch_patterns(self) -> None:
        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

        self.assertTrue(user_asks_kb_fetch_or_fill("get the firm address from knowledge base"))
        self.assertTrue(user_asks_kb_fetch_or_fill("search KB for Sonja contact info"))
        self.assertTrue(user_asks_kb_fetch_or_fill("look up certifications in the knowledge base"))
        self.assertFalse(user_asks_kb_fetch_or_fill("Does this meet the RFP?"))
        self.assertFalse(
            user_asks_kb_fetch_or_fill(
                "replace Hampton Lumber case study with Bend from KB"
            )
        )

    def test_remove_verify_fill_or_remove_detected(self) -> None:
        from app.services.proposal_section_editor import _open_tab_verify_resolve_ask
        from app.services.proposal_verify_optional_scrub import (
            user_asks_scrub_optional_verify,
            user_asks_strip_inline_evidence_tags,
        )

        msg = "remove verify tags fill them or remvoe them"
        self.assertTrue(_open_tab_verify_resolve_ask(msg))
        self.assertTrue(user_asks_scrub_optional_verify(msg))
        self.assertTrue(user_asks_strip_inline_evidence_tags(msg))

    def test_kb_fetch_skips_edit_scope_planning(self) -> None:
        from app.services.proposal_section_editor import _open_tab_kb_fetch_ask

        msg = "here San Francisco Travel case study is empty fetch thst"
        self.assertTrue(_open_tab_kb_fetch_ask(msg))

    def test_check_then_fix_is_edit(self) -> None:
        self.assertTrue(
            _wants_section_edit(
                "check which case studies fail the RFP then replace the weak ones from KB"
            )
        )


if __name__ == "__main__":
    unittest.main()
