"""Tests for packet redistribute mechanical moves."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_packet_redistribute import (
    apply_move_block,
    apply_move_tab,
    build_place_preview,
    execute_redistribute_plan,
    extract_heading_block,
)


def _draft(*pairs: tuple[str, str, str]) -> ProposalDraft:
    sections = [
        ProposalSection(
            id=sid,
            title=title,
            content=content,
            source="rfp",
            mode="write",
            wordTarget=200,
            status="generated",
        )
        for sid, title, content in pairs
    ]
    return ProposalDraft(
        rfpId="rfp-redist",
        sections=sections,
        updatedAt="2026-08-27T00:00:00+00:00",
    )


class ExtractHeadingBlockTests(unittest.TestCase):
    def test_extract_heading_block_moves_until_next_h2(self) -> None:
        body = "## Intro\nHi\n\n## Approach\nDo X\n\n## Close\nBye\n"
        hit = extract_heading_block(body, "Approach")
        self.assertIsNotNone(hit)
        assert hit is not None
        before, block, after = hit
        self.assertIn("## Approach", block)
        self.assertIn("Do X", block)
        self.assertIn("## Close", after)
        self.assertIn("## Intro", before)


class MoveBlockTests(unittest.TestCase):
    def test_apply_move_block_appends_and_removes(self) -> None:
        draft = _draft(
            ("a", "A", "## Intro\nHi\n\n## Approach\nDo X\n\n## Close\nBye\n"),
            ("b", "B", "## B start\nKeep\n"),
        )
        updated, log = apply_move_block(
            draft, from_id="a", to_id="b", heading_text="Approach"
        )
        a = next(s for s in updated.sections if s.id == "a")
        b = next(s for s in updated.sections if s.id == "b")
        self.assertNotIn("Approach", a.content or "")
        self.assertIn("Do X", b.content or "")
        self.assertIn("Approach", b.content or "")
        self.assertIn("moved heading", log)


class MoveTabTests(unittest.TestCase):
    def test_apply_move_tab_reorders(self) -> None:
        draft = _draft(
            ("a", "A", "a"),
            ("b", "B", "b"),
            ("c", "C", "c"),
        )
        updated, _ = apply_move_tab(draft, section_id="c", after_section_id="a")
        ids = [s.id for s in updated.sections]
        self.assertEqual(ids, ["a", "c", "b"])


class ExecutePlanTests(unittest.TestCase):
    def test_execute_plan_moves_block_without_llm(self) -> None:
        draft = _draft(
            ("a", "A", "## Intro\nHi\n\n## Approach\nDo X\n"),
            ("b", "B", "## Other\nY\n"),
        )
        plan = {
            "ops": [
                {
                    "op": "move_block",
                    "fromSectionId": "a",
                    "toSectionId": "b",
                    "match": {"type": "heading", "text": "Approach"},
                }
            ],
            "stubFillIds": [],
            "humanGaps": [],
        }
        updated, logs = execute_redistribute_plan(
            draft, plan, allow_static_reorder=False
        )
        b = next(s for s in updated.sections if s.id == "b")
        a = next(s for s in updated.sections if s.id == "a")
        self.assertIn("Approach", b.content or "")
        self.assertNotIn("Approach", a.content or "")
        self.assertTrue(any("moved heading" in x for x in logs))

    def test_static_move_tab_ignored_when_flag_false(self) -> None:
        draft = _draft(
            ("section-1-who", "Who We Are", "brand"),
            ("rfp-tab", "Approach", "x"),
        )
        plan = {
            "ops": [
                {
                    "op": "move_tab",
                    "sectionId": "section-1-who",
                    "afterSectionId": "rfp-tab",
                }
            ]
        }
        updated, logs = execute_redistribute_plan(
            draft, plan, allow_static_reorder=False
        )
        self.assertEqual([s.id for s in updated.sections], ["section-1-who", "rfp-tab"])
        self.assertTrue(any("static locked" in x for x in logs))


class PlacePreviewTests(unittest.TestCase):
    def test_build_place_preview_lists_moves_and_issues(self) -> None:
        draft = _draft(
            ("a", "Who We Are", "## Intro\nHi\n\n## Approach\nDo X\n"),
            ("b", "Technical Approach", "## Other\nY\n"),
            ("c", "Empty Tab", "[MANUAL FILL: write this]\n"),
        )
        plan = {
            "ops": [
                {
                    "op": "move_block",
                    "fromSectionId": "a",
                    "toSectionId": "b",
                    "match": {"type": "heading", "text": "Approach"},
                }
            ],
            "stubFillIds": ["c"],
            "humanGaps": ["Paste §6.3 format page"],
        }
        preview = build_place_preview(draft, plan)
        self.assertEqual(preview["plannedMoves"], 1)
        self.assertTrue(any("Approach" in x for x in preview["issues"]))
        self.assertEqual(preview["moves"][0]["fromTitle"], "Who We Are")
        self.assertEqual(preview["moves"][0]["toTitle"], "Technical Approach")
        self.assertIn("Empty Tab", preview["stubTitles"])
        self.assertIn("Paste §6.3 format page", preview["humanGaps"])

    def test_build_place_preview_empty_plan_explains_next_step(self) -> None:
        draft = _draft(("a", "A", "## Only\nHi\n"))
        preview = build_place_preview(
            draft, {"ops": [], "stubFillIds": [], "humanGaps": []}
        )
        self.assertEqual(preview["plannedMoves"], 0)
        self.assertTrue(preview["nothingToMove"])
        self.assertEqual(preview["issues"], [])
        self.assertIn("No writing needs moving", preview["summary"])


if __name__ == "__main__":
    unittest.main()
