"""Improve-full-section must rewrite the tab — not die as a failed selection patch.

Regression: Exhibit 1 issue lists (missing I.2 Active Client List, empty headers,
§18/§26 padding) were collapsed by edit-scope into one selection edit that left
the excerpt unchanged → DRAFT UNCHANGED + salvage budget noise. Active client
lists already live in other tabs and must be copyable into the required I.2 slot.
"""

from __future__ import annotations

import unittest

from app.services.proposal_chat_improve_pin import (
    extract_active_client_list_block,
    fill_active_client_list_from_siblings,
    fill_all_active_client_lists_from_siblings,
    improve_pin_needs_full_rewrite,
    insert_all_board_roster_verify_flags,
    insert_board_roster_verify_flag,
    insert_missing_active_client_list,
    should_collapse_edit_scope_to_selection,
)
from app.models.proposal import ProposalDraft, ProposalSection



def _sec(id_: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=id_,
        title=title,
        content=content,
        word_target=500,
        required=True,
        custom=False,
        status="generated",
        source="template",
    )


ISSUE_LIST = """
I.2 Active Client List entirely missing — jumps I.1 → I.3, no client list.
This is the core defect that makes §18 a broken duplicate of §26.
Stray double period — "...professional liability insurance. ."
Dangling empty header — "State registrations." with nothing under it.
Empty table cells in I.4 — 3 of 7 rows have no Cost to CNM / When billed values.
Fix all of these in this section.
""".strip()


class ImprovePinFullRewriteTests(unittest.TestCase):
    def test_issue_list_with_missing_i2_needs_full_rewrite(self) -> None:
        body = "## I.1 Position\nWe serve CNM.\n\n## I.3 Insurance\nLiability insurance. .\n"
        self.assertTrue(improve_pin_needs_full_rewrite(ISSUE_LIST, body))

    def test_simple_tighten_does_not_force_full_rewrite(self) -> None:
        self.assertFalse(
            improve_pin_needs_full_rewrite(
                "make this tighter",
                "## I.1 Position\nShort prose.\n",
            )
        )

    def test_improve_pin_never_collapses_structural_ask_to_selection(self) -> None:
        self.assertFalse(
            should_collapse_edit_scope_to_selection(
                improve_section_pinned=True,
                user_message=ISSUE_LIST,
                section_content="## I.1\nx\n\n## I.3\ny\n",
                planned_span_count=1,
            )
        )

    def test_improve_pin_never_collapses_even_for_soft_asks(self) -> None:
        self.assertFalse(
            should_collapse_edit_scope_to_selection(
                improve_section_pinned=True,
                user_message="fix the double period after insurance.",
                section_content="Liability insurance. .",
                planned_span_count=1,
            )
        )

    def test_unpinned_single_patch_may_still_collapse(self) -> None:
        self.assertTrue(
            should_collapse_edit_scope_to_selection(
                improve_section_pinned=False,
                user_message="fix the double period after insurance.",
                section_content="Liability insurance. .",
                planned_span_count=1,
            )
        )

    def test_improve_pin_multi_patch_does_not_collapse_to_selection(self) -> None:
        # Multi-patch stays on the multi-patch path (selection_mode must stay false).
        self.assertFalse(
            should_collapse_edit_scope_to_selection(
                improve_section_pinned=True,
                user_message="Fix the three typos in this section.",
                section_content="a. . b. . c. .",
                planned_span_count=3,
            )
        )


class ActiveClientListCopyTests(unittest.TestCase):
    def test_extract_active_client_list_block(self) -> None:
        src = (
            "## References\n"
            "Intro.\n\n"
            "## Active Client List\n"
            "| Client | Contact |\n| --- | --- |\n| City of Umatilla | Jane |\n\n"
            "## Next\n"
            "Other."
        )
        block = extract_active_client_list_block(src)
        assert block is not None
        self.assertIn("City of Umatilla", block)
        self.assertNotIn("## Next", block)

    def test_insert_missing_i2_between_i1_and_i3(self) -> None:
        body = (
            "## I.1 Position\n"
            "We can deliver.\n\n"
            "## I.3 Insurance Certification\n"
            "Liability insurance.\n"
        )
        client_block = (
            "## Active Client List\n"
            "| Client | Contact |\n| --- | --- |\n| Deschutes | Sam |\n"
        )
        out = insert_missing_active_client_list(body, client_block)
        assert out is not None
        self.assertIn("## I.2 Active Client List", out)
        self.assertIn("Deschutes", out)
        # Order: I.1 then I.2 then I.3
        self.assertLess(out.index("## I.1"), out.index("## I.2 Active Client List"))
        self.assertLess(out.index("## I.2 Active Client List"), out.index("## I.3"))

    def test_fill_from_sibling_when_exhibit_skips_i2(self) -> None:
        exhibit = _sec(
            "exhibit-1",
            "Exhibit 1: Evaluation Criteria Response Form",
            "## I.1 Position\nReady.\n\n## I.3 Insurance\nOk.\n",
        )
        refs = _sec(
            "refs",
            "21. References — Current Clients",
            "## Active Client List\n"
            "| Client | Contact |\n| --- | --- |\n| Oregon Employment | Pat |\n",
        )
        updated = fill_active_client_list_from_siblings(exhibit, [exhibit, refs])
        assert updated is not None
        self.assertIn("## I.2 Active Client List", updated.content or "")
        self.assertIn("Oregon Employment", updated.content or "")

    def test_no_fill_when_i2_already_present(self) -> None:
        exhibit = _sec(
            "exhibit-1",
            "Exhibit 1",
            "## I.1\nA\n\n## I.2 Active Client List\nAlready here.\n\n## I.3\nB\n",
        )
        refs = _sec(
            "refs",
            "References",
            "## Active Client List\n| Client |\n| City |\n",
        )
        self.assertIsNone(fill_active_client_list_from_siblings(exhibit, [exhibit, refs]))

    def test_draft_wide_fill_for_scan_and_generate(self) -> None:
        exhibit = _sec(
            "exhibit-1",
            "Exhibit 1: Evaluation Criteria Response Form",
            "## I.1 Position\nReady.\n\n## I.3 Insurance\nOk.\n",
        )
        refs = _sec(
            "refs",
            "21. References — Current Clients",
            "## Active Client List\n"
            "| Client | Contact |\n| --- | --- |\n| Oregon Employment | Pat |\n",
        )
        other = _sec("who", "1.1 — Who We Are", "Agency bio.")
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-08-26T00:00:00Z",
            sections=[exhibit, refs, other],
        )
        updated, logs = fill_all_active_client_lists_from_siblings(draft)
        self.assertTrue(logs)
        self.assertIn("missing I.2", logs[0])
        self.assertIn("Exhibit 1", logs[0])
        exhibit_out = next(s for s in updated.sections if s.id == "exhibit-1")
        self.assertIn("## I.2 Active Client List", exhibit_out.content or "")
        self.assertIn("Oregon Employment", exhibit_out.content or "")
        # Donor unchanged
        refs_out = next(s for s in updated.sections if s.id == "refs")
        self.assertEqual(refs_out.content, refs.content)


class BoardRosterVerifyInsertTests(unittest.TestCase):
    def test_inserts_verify_above_board_list_on_campaign_disclosure(self) -> None:
        body = (
            "## Exhibit 5, Campaign Contribution Disclosure\n"
            "*Submitted in compliance with New Mexico…*\n\n"
            "### Governing Board Members\n"
            "- Alarid\n- Chavez\n- Swisstack\n\n"
            "| Question | Response |\n| --- | --- |\n| Contributions | None |\n"
        )
        section = _sec(
            "exhibit-5",
            "Exhibit 5 — Campaign Contribution Disclosure",
            body,
        )
        out = insert_board_roster_verify_flag(section)
        assert out is not None
        self.assertIn("[VERIFY: Ella / Rachel", out.content or "")
        self.assertIn("board roster", (out.content or "").casefold())
        # Inserted before the board heading / names
        verify_at = (out.content or "").index("[VERIFY:")
        board_at = (out.content or "").casefold().index("alarid")
        self.assertLess(verify_at, board_at)
        # Disclosure answers unchanged
        self.assertIn("| Contributions | None |", out.content or "")

    def test_no_duplicate_when_verify_already_present(self) -> None:
        body = (
            "## Campaign Contribution Disclosure\n"
            "[VERIFY: Ella / Rachel — confirm current board roster before submission]\n\n"
            "### Board of Trustees\n"
            "- Alarid\n- Chavez\n"
        )
        section = _sec("e5", "Exhibit 5 — Campaign Contribution Disclosure", body)
        self.assertIsNone(insert_board_roster_verify_flag(section))

    def test_skips_unrelated_sections(self) -> None:
        section = _sec(
            "who",
            "1.1 — Who We Are",
            "zö agency is a full-service creative group in Bend.",
        )
        self.assertIsNone(insert_board_roster_verify_flag(section))

    def test_draft_wide_scan_helper(self) -> None:
        e5 = _sec(
            "exhibit-5",
            "Exhibit 5 — Campaign Contribution Disclosure",
            "## Board of Trustees\n- Alarid\n- Baca\n- Trujillo\n",
        )
        who = _sec("who", "Who We Are", "Bio.")
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-08-26T00:00:00Z",
            sections=[e5, who],
        )
        updated, logs = insert_all_board_roster_verify_flags(draft)
        self.assertTrue(logs)
        e5_out = next(s for s in updated.sections if s.id == "exhibit-5")
        self.assertIn("[VERIFY:", e5_out.content or "")
        who_out = next(s for s in updated.sections if s.id == "who")
        self.assertEqual(who_out.content, who.content)


if __name__ == "__main__":
    unittest.main()
