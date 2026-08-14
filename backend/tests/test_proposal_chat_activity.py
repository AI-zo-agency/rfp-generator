"""Agent recap payload for section chat."""

from __future__ import annotations

import unittest

from app.services.proposal_chat_activity import build_improve_agent_activity


class ImproveAgentActivityTests(unittest.TestCase):
    def test_none_found_when_clean_edit(self) -> None:
        activity = build_improve_agent_activity(
            section_title="Cost Proposal",
            before="Fees {{budget.professional_fees}}",
            after="Fees $50,000",
            draft_changed=True,
            assistant_message="Filled from the ledger.",
        )
        self.assertEqual(activity.outcome, "ok")
        self.assertTrue(any("Updated" in c for c in activity.changes))
        self.assertEqual(activity.discrepancies, [])

    def test_flags_unresolved_budget_tokens(self) -> None:
        activity = build_improve_agent_activity(
            section_title="Cost Proposal",
            before="{{budget.media_oahu}}",
            after="{{budget.media_oahu}}",
            draft_changed=False,
        )
        self.assertEqual(activity.outcome, "needs_review")
        self.assertTrue(any("{{budget." in d for d in activity.discrepancies))
        self.assertIn("No manuscript text was changed.", activity.changes)


if __name__ == "__main__":
    unittest.main()
