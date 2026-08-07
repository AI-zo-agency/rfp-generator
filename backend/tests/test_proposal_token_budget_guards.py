"""Token-budget guards for Senior Editor / Phase-3 drafting (quality-safe caps)."""

from __future__ import annotations

import inspect
import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_drafting_graph import _evidence_for_section, _format_evidence_block
from app.services.proposal_knowledge_base_tools import SEARCH_CHARACTER_LIMIT
from app.services.proposal_self_edit_loop import (
    _apply_senior_editor_tickets,
    _manuscript_digest_for_senior_editor,
)


class TokenBudgetGuardTests(unittest.TestCase):
    def test_kb_search_default_is_bounded(self) -> None:
        self.assertLessEqual(SEARCH_CHARACTER_LIMIT, 24_000)

    def test_manuscript_digest_uses_short_excerpts(self) -> None:
        sections = [
            ProposalSection(
                id=f"s-{i}",
                title=f"Section {i}",
                content=("word " * 800),
            )
            for i in range(8)
        ]
        digest = _manuscript_digest_for_senior_editor(
            ProposalDraft(
                rfp_id="rfp-x",
                sections=sections,
                updated_at="2026-01-01T00:00:00Z",
            )
        )
        self.assertLessEqual(len(digest), 35_000)
        # Per-section head should be ~1200 words of content, not 2200+.
        for block in digest.split("### ")[1:]:
            body = block.split("\n", 1)[-1]
            self.assertLessEqual(len(body.strip()), 1300)

    def test_evidence_block_is_compact(self) -> None:
        corpus = [
            {
                "id": f"E{i}",
                "source": "doc",
                "excerpt": "x" * 5000,
                "sectionIds": ["sec-1"],
            }
            for i in range(30)
        ]
        items = _evidence_for_section("sec-1", corpus)
        self.assertLessEqual(len(items), 12)
        block = _format_evidence_block(items)
        self.assertNotIn("x" * 1800, block)

    def test_apply_tickets_default_max_is_five(self) -> None:
        sig = inspect.signature(_apply_senior_editor_tickets)
        self.assertEqual(sig.parameters["max_tickets"].default, 5)


if __name__ == "__main__":
    unittest.main()
