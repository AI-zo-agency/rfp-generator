"""Lean senior-editor path — skip LLM emit when manuscript looks clean."""

from __future__ import annotations

import unittest

from app.core.step_debug_logger import pipeline_phase, pipeline_step, resolve_pipeline_node_name
from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_self_edit_loop import (
    _empty_senior_editor_tickets,
    _manuscript_needs_senior_llm_emit,
)


def _draft(*sections: tuple[str, str]) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(id=sid, title=title, content=body)
            for sid, title, body in (
                (s[0], s[0], s[1]) for s in sections
            )
        ],
        updatedAt="2026-01-01T00:00:00Z",
    )


class SeniorEditorLeanTests(unittest.TestCase):
    def test_skip_emit_when_manuscript_clean(self) -> None:
        body = " ".join(["word"] * 120)
        draft = _draft(("s1", body), ("s2", body))
        self.assertFalse(
            _manuscript_needs_senior_llm_emit(
                draft,
                verify_tag_count=0,
                mechanical_coverage=[],
                dedupe_logs=[],
            )
        )

    def test_emit_when_verify_tags_present(self) -> None:
        draft = _draft(("s1", "ok prose " * 40))
        self.assertTrue(
            _manuscript_needs_senior_llm_emit(
                draft,
                verify_tag_count=2,
                mechanical_coverage=[],
                dedupe_logs=[],
            )
        )

    def test_empty_tickets_shape(self) -> None:
        tickets = _empty_senior_editor_tickets()
        self.assertEqual(tickets["coverageTickets"], [])
        self.assertEqual(tickets["complianceTickets"], [])
        self.assertEqual(tickets["deleteSectionTickets"], [])

    def test_senior_editor_prompt_forbids_deleting_tabs(self) -> None:
        from pathlib import Path

        prompt_src = Path(__file__).resolve().parents[1] / "app" / "services" / "proposal_langchain_agents.py"
        text = prompt_src.read_text(encoding="utf-8")
        self.assertIn("NEVER DELETE A TOC TAB", text)
        self.assertIn("deleteSectionTickets MUST always be []", text)
        self.assertNotIn("Prefer deleting content clones", text)


class PipelineNodeNameTests(unittest.TestCase):
    def test_resolve_from_phase_and_step(self) -> None:
        with pipeline_phase("phase-3", rfp_id="r1"):
            with pipeline_step("draft_sections"):
                self.assertEqual(resolve_pipeline_node_name(), "phase-3:draft_sections")

    def test_resolve_explicit_wins(self) -> None:
        with pipeline_phase("phase-2"):
            self.assertEqual(resolve_pipeline_node_name("custom"), "custom")


if __name__ == "__main__":
    unittest.main()
