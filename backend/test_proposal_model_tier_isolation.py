"""Unit tests for LLM tier routing and section isolation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_isolation import (
    SectionIsolationError,
    assert_only_section_changed,
    replace_section_isolated,
    snapshot_section_contents,
)


def _section(sid: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=sid,
        content=content,
        wordTarget=100,
        mode="write",
        status="generated",
    )


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        updatedAt="2026-07-21T00:00:00Z",
        sections=list(sections),
    )


class ResolveLlmModelTests(unittest.TestCase):
    def test_heavy_uses_openrouter_when_heavy_empty(self) -> None:
        from app.services.llm import resolve_llm_model

        with patch("app.services.llm.settings") as settings:
            settings.llm_heavy_model = ""
            settings.llm_light_model = "anthropic/claude-haiku-4.5"
            settings.openrouter_model = "anthropic/claude-sonnet-4"
            self.assertEqual(resolve_llm_model("heavy"), "anthropic/claude-sonnet-4")

    def test_light_uses_haiku(self) -> None:
        from app.services.llm import resolve_llm_model

        with patch("app.services.llm.settings") as settings:
            settings.llm_heavy_model = "anthropic/claude-sonnet-4"
            settings.llm_light_model = "anthropic/claude-haiku-4.5"
            settings.openrouter_model = "anthropic/claude-sonnet-4"
            self.assertEqual(resolve_llm_model("light"), "anthropic/claude-haiku-4.5")

    def test_light_falls_back_to_heavy_when_empty(self) -> None:
        from app.services.llm import resolve_llm_model

        with patch("app.services.llm.settings") as settings:
            settings.llm_heavy_model = "anthropic/claude-sonnet-4"
            settings.llm_light_model = ""
            settings.openrouter_model = "anthropic/claude-sonnet-4"
            self.assertEqual(resolve_llm_model("light"), "anthropic/claude-sonnet-4")


class SectionIsolationTests(unittest.TestCase):
    def test_replace_only_target(self) -> None:
        draft = _draft(
            _section("cover", "cover letter"),
            _section("who", "who we are"),
        )
        updated = _section("who", "who we are — trimmed")
        next_draft = replace_section_isolated(draft, updated)
        self.assertEqual(next_draft.sections[0].content, "cover letter")
        self.assertEqual(next_draft.sections[1].content, "who we are — trimmed")

    def test_assert_fails_when_sibling_changes(self) -> None:
        before = {"cover": "A", "who": "B"}
        after = _draft(
            _section("cover", "CHANGED"),
            _section("who", "B2"),
        )
        with self.assertRaises(SectionIsolationError):
            assert_only_section_changed(before, after, allowed_section_id="who")

    def test_snapshot_keys(self) -> None:
        draft = _draft(_section("a", "1"), _section("b", "2"))
        self.assertEqual(snapshot_section_contents(draft), {"a": "1", "b": "2"})


if __name__ == "__main__":
    unittest.main()
