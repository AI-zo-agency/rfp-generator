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

    def test_flags_apply_fix_that_changed_nothing(self) -> None:
        """A swallowed Apply click must read as needs_review, not a clean run."""
        activity = build_improve_agent_activity(
            section_title="1.1 — Who We Are",
            before="same",
            after="same",
            draft_changed=False,
            assistant_message="Reviewed certification claims. None fabricated.",
            user_message="Apply the fix: replace the certifications paragraph.",
            apply_fix=True,
        )
        self.assertEqual(activity.outcome, "needs_review")
        self.assertTrue(any("not applied" in d.casefold() for d in activity.discrepancies))

    def test_unchanged_chat_turn_is_not_flagged(self) -> None:
        activity = build_improve_agent_activity(
            section_title="1.1 — Who We Are",
            before="same",
            after="same",
            draft_changed=False,
            assistant_message="Nothing to change here.",
            user_message="Does this section cite our certifications?",
        )
        self.assertEqual(activity.outcome, "unchanged")
        self.assertEqual(activity.discrepancies, [])

    def test_flags_invented_mfill_tokens(self) -> None:
        activity = build_improve_agent_activity(
            section_title="Firm Qualifications",
            before="## Scored Capability\n| x | y |",
            after="| «MFILL_1» | «MFILL_2» |",
            draft_changed=True,
        )
        self.assertEqual(activity.outcome, "needs_review")
        self.assertTrue(any("«MFILL_" in d for d in activity.discrepancies))

    def test_flags_partial_selection_when_ask_needs_full_section(self) -> None:
        from app.services.proposal_chat_activity import collect_chat_edit_discrepancies

        notes = collect_chat_edit_discrepancies(
            before="## A\nold",
            after="## A\nnew",
            user_message="Fix all these issues: empty table cells, missing subsection.",
            assistant_message=(
                "Revised only your selected excerpt in Firm Qualifications. "
                "The rest of the section is unchanged."
            ),
        )
        self.assertTrue(any("full-section" in n.casefold() for n in notes))

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
