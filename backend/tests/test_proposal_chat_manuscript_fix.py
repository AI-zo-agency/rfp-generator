"""Manuscript multi-patch helpers — plan-driven, not keyword-gated."""

from __future__ import annotations

import unittest

from app.services.proposal_chat_manuscript_fix import (
    _fix_wants_kb,
    _format_recent_chat,
    _plan_wants_budget_agent,
)


class FormatRecentChatTests(unittest.TestCase):
    def test_includes_prior_assistant_callouts(self) -> None:
        blob = _format_recent_chat(
            [
                {
                    "role": "assistant",
                    "content": (
                        "Issues: fee column has phones; GSU leftovers in bios; "
                        "missing Offeror Commitment section."
                    ),
                },
                {"role": "user", "content": "ok noted"},
            ]
        )
        self.assertIn("GSU leftovers", blob)
        self.assertIn("Offeror Commitment", blob)
        self.assertIn("ASSISTANT:", blob)

    def test_empty_history(self) -> None:
        self.assertEqual(_format_recent_chat(None), "(no prior chat)")
        self.assertEqual(_format_recent_chat([]), "(no prior chat)")


class PlanFlagTests(unittest.TestCase):
    def test_budget_agent_honors_planner_bool(self) -> None:
        self.assertTrue(_plan_wants_budget_agent({"runBudgetAgent": True}))
        self.assertFalse(_plan_wants_budget_agent({"runBudgetAgent": False}))
        self.assertFalse(_plan_wants_budget_agent({}))
        self.assertFalse(
            _plan_wants_budget_agent(
                {"fixes": [{"sectionId": "rfp-sec-2", "brief": "clean case studies"}]}
            )
        )

    def test_kb_excerpts_honors_per_fix_flag(self) -> None:
        self.assertTrue(_fix_wants_kb({"needsKbExcerpts": True}))
        self.assertFalse(_fix_wants_kb({"needsKbExcerpts": False}))
        self.assertFalse(
            _fix_wants_kb(
                {"brief": "Remove Recovery Network FLAG — wrong RFP leftover"}
            )
        )


if __name__ == "__main__":
    unittest.main()
