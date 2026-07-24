"""Cross-tab section resolution + edit-scope anchor location."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_editor import (
    EditScopePatch,
    _locate_anchor_in_content,
    _locate_planned_patches,
    _merge_overlapping_located_patches,
    _parse_edit_scope_patches,
    _resolve_section_from_message,
)


def _sec(sid: str, title: str, content: str, *, source: str = "rfp") -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        source=source,
        mode="write",
        wordTarget=400,
        status="generated",
    )


class ResolveSectionFromMessageTests(unittest.TestCase):
    def test_quoted_claim_routes_to_other_section(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                _sec(
                    "section-2-bio-shawn",
                    "2.5 — Shawn Dicriscio",
                    "Shawn leads creative.",
                    source="template",
                ),
                _sec(
                    "rfp-sec-9",
                    "Project Staffing Plan",
                    "We use a 10-year corporate-creative partnership model with partners.",
                ),
            ],
            updatedAt="2026-07-23T00:00:00+00:00",
        )
        hit = _resolve_section_from_message(
            draft,
            "Section 30 references a '10-year corporate-creative partnership model' "
            "— source from KB or remove it",
            "section-2-bio-shawn",
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.id, "rfp-sec-9")

    def test_hyphen_year_phrase_without_quotes(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                _sec("focus", "Company Overview", "Overview text.", source="rfp"),
                _sec(
                    "rfp-sec-9",
                    "Project Staffing Plan",
                    "Our 10-year corporate-creative partnership model is unique.",
                ),
            ],
            updatedAt="2026-07-23T00:00:00+00:00",
        )
        hit = _resolve_section_from_message(
            draft,
            "Fix the 10-year corporate-creative partnership model claim",
            "focus",
        )
        self.assertEqual(hit.id if hit else None, "rfp-sec-9")


class LocateAnchorTests(unittest.TestCase):
    def test_anchor_maps_to_paragraph_not_whole_section(self) -> None:
        content = (
            "## Reference 1: Oregon Employment Department\n\n"
            "Contact details here.\n\n"
            "We use a 10-year corporate-creative partnership model with partners.\n\n"
            "## Reference 2: University of Idaho\n\n"
            "More reference text that should stay untouched."
        )
        span = _locate_anchor_in_content(
            content,
            "We use a 10-year corporate-creative partnership model with partners.",
        )
        self.assertIsNotNone(span)
        assert span is not None
        start, end = span
        excerpt = content[start:end]
        self.assertIn("10-year corporate-creative partnership model", excerpt)
        self.assertNotIn("University of Idaho", excerpt)
        self.assertLess(end - start, len(content) // 2)

    def test_whole_section_anchor_rejected(self) -> None:
        content = (
            "Paragraph one stays.\n\n"
            "Paragraph two stays.\n\n"
            "Paragraph three stays as well for length."
        )
        span = _locate_anchor_in_content(content, content)
        self.assertIsNone(span)

    def test_tight_keeps_sibling_sentences_separate(self) -> None:
        content = (
            "Ron Comer allocates 25% of his time specifically to GSU ARCHI work. "
            "No team member carries more than 60% of any critical project component. "
            "Coverage stays continuous."
        )
        a = _locate_anchor_in_content(
            content,
            "Ron Comer allocates 25% of his time specifically to GSU ARCHI work.",
            tight=True,
        )
        b = _locate_anchor_in_content(
            content,
            "No team member carries more than 60% of any critical project component.",
            tight=True,
        )
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        assert a is not None and b is not None
        self.assertIn("25%", content[a[0] : a[1]])
        self.assertNotIn("60%", content[a[0] : a[1]])
        self.assertIn("60%", content[b[0] : b[1]])
        self.assertNotIn("25%", content[b[0] : b[1]])


class EditScopeMultiPatchTests(unittest.TestCase):
    def test_parse_patches_array(self) -> None:
        patches = _parse_edit_scope_patches(
            {
                "patches": [
                    {
                        "anchorExcerpt": "Ron Comer allocates 25% of his time specifically.",
                        "editorInstruction": "Remove the 25% figure; use qualitative language.",
                    },
                    {
                        "anchorExcerpt": "No team member carries more than 60% of any critical.",
                        "editorInstruction": "Remove the 60% figure; use qualitative language.",
                    },
                ]
            },
            "remove unsourced percentages",
        )
        self.assertEqual(len(patches), 2)
        self.assertIn("25%", patches[0].anchor_excerpt)

    def test_parse_legacy_single_anchor(self) -> None:
        patches = _parse_edit_scope_patches(
            {
                "anchorExcerpt": "Our 10-year corporate-creative partnership model",
                "editorInstruction": "Replace 10-year with 13-year.",
            },
            "fix years",
        )
        self.assertEqual(len(patches), 1)
        self.assertIn("10-year", patches[0].anchor_excerpt)

    def test_locate_planned_patches_finds_all_three(self) -> None:
        content = (
            "TIME ALLOCATION AND AVAILABILITY\n\n"
            "Ron Comer allocates 25% of his time specifically to GSU ARCHI work. "
            "Creative leads stay focused on delivery. "
            "No team member carries more than 60% of any critical project component.\n\n"
            "PROJECT CONTINUITY ASSURANCE\n\n"
            "OUR 10-YEAR CORPORATE-CREATIVE PARTNERSHIP MODEL keeps continuity strong."
        )
        patches = [
            EditScopePatch(
                "Ron Comer allocates 25% of his time specifically to GSU ARCHI work.",
                "Remove 25%; qualitative.",
            ),
            EditScopePatch(
                "No team member carries more than 60% of any critical project component.",
                "Remove 60%; qualitative.",
            ),
            EditScopePatch(
                "OUR 10-YEAR CORPORATE-CREATIVE PARTNERSHIP MODEL keeps continuity strong.",
                "Replace 10-year with 13-year.",
            ),
        ]
        located = _locate_planned_patches(content, patches)
        self.assertEqual(len(located), 3)
        joined = " | ".join(content[s:e] for s, e, _ in located)
        self.assertIn("25%", joined)
        self.assertIn("60%", joined)
        self.assertIn("10-YEAR", joined)

    def test_merge_overlapping_combines_instructions(self) -> None:
        p1 = EditScopePatch("aaaa", "Fix A")
        p2 = EditScopePatch("bbbb", "Fix B")
        merged = _merge_overlapping_located_patches(
            [(10, 40, p1), (30, 55, p2)],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0][0], 10)
        self.assertEqual(merged[0][1], 55)
        self.assertIn("Fix A", merged[0][2].editor_instruction)
        self.assertIn("Fix B", merged[0][2].editor_instruction)


if __name__ == "__main__":
    unittest.main()
