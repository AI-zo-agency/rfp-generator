"""Excerpt revision must actually see the context assembled for it.

load_rfp_for_proposal() and improve_proposal_section() append HARD FACTS, the
mapped section requirements, the full-proposal manuscript digest and the pricing
guide onto rfp_context — after up to 50k chars of raw RFP body. The excerpt
rewriter used to read rfp_context[:2000], so every one of those blocks landed
past the cut and the model revised text with nothing but the RFP letterhead.
"""

from __future__ import annotations

import unittest

from app.services.proposal_section_editor import _budget_rfp_context

HARD_FACTS = (
    "## HARD FACTS (from full RFP text — cite exactly; never invent 'undisclosed')\n"
    "### Contract value / ceiling\n"
    "- Not-to-exceed $250,000 over three years.\n"
    "### Evaluation criteria (points)\n"
    "- Approach and methodology: 40 points\n"
)
REQUIREMENTS = (
    "--- Mapped section requirements ---\n"
    "- Provide legal name, DBA, EIN, and years in operation.\n"
    "- List office locations serving the district.\n"
)
DIGEST = (
    "FULL PROPOSAL MANUSCRIPT (every section — use this for whole-proposal answers):\n"
    "\n### 1.1 Cover Letter\nWe are pleased to submit...\n"
    "\n### 1.3 Business Information\nLegal Name: Z'Onion Creative Group LLC\n"
)
PRICING = (
    "=== 00_Guide_Pricing (Supermemory) ===\n"
    "Creative Director $185/hr. Senior Designer $140/hr.\n"
)

# 50k of RFP body is the real ceiling (RFP_PROMPT_MAX_CHARS), and it is
# concatenated *before* every block above.
RFP_BODY = "Title: Kalamazoo Valley CC RFP\nClient: KVCC\n\n" + ("RFP boilerplate. " * 4000)

FULL_CONTEXT = "\n\n".join([RFP_BODY, HARD_FACTS, REQUIREMENTS, DIGEST, PRICING])


class BudgetRfpContextTests(unittest.TestCase):
    def test_old_positional_slice_loses_every_appended_block(self) -> None:
        """Documents the bug: this is what the rewriter used to receive."""
        legacy = FULL_CONTEXT[:2000]
        self.assertNotIn("HARD FACTS", legacy)
        self.assertNotIn("Mapped section requirements", legacy)
        self.assertNotIn("FULL PROPOSAL MANUSCRIPT", legacy)
        self.assertNotIn("00_Guide_Pricing", legacy)

    def test_every_appended_block_survives_budgeting(self) -> None:
        budgeted = _budget_rfp_context(FULL_CONTEXT)
        self.assertIn("Not-to-exceed $250,000", budgeted)
        self.assertIn("Approach and methodology: 40 points", budgeted)
        self.assertIn("Provide legal name, DBA, EIN", budgeted)
        self.assertIn("FULL PROPOSAL MANUSCRIPT", budgeted)
        self.assertIn("Creative Director $185/hr", budgeted)

    def test_rfp_body_head_is_kept_but_bounded(self) -> None:
        budgeted = _budget_rfp_context(FULL_CONTEXT)
        self.assertIn("Kalamazoo Valley CC RFP", budgeted)
        # The body must not crowd out the blocks that follow it.
        self.assertLess(budgeted.index("HARD FACTS"), 2_400)

    def test_total_stays_within_a_predictable_ceiling(self) -> None:
        self.assertLessEqual(len(_budget_rfp_context(FULL_CONTEXT)), 20_000)

    def test_oversized_block_is_truncated_not_dropped(self) -> None:
        huge_digest = (
            "FULL PROPOSAL MANUSCRIPT (every section — use this for whole-proposal answers):\n"
            + ("section text " * 6000)
        )
        budgeted = _budget_rfp_context("\n\n".join([RFP_BODY, HARD_FACTS, huge_digest, PRICING]))
        self.assertIn("FULL PROPOSAL MANUSCRIPT", budgeted)
        # A later block must still survive an oversized earlier one.
        self.assertIn("Creative Director $185/hr", budgeted)
        self.assertIn("truncated", budgeted)

    def test_context_with_no_appended_blocks_is_just_the_body(self) -> None:
        budgeted = _budget_rfp_context(RFP_BODY)
        self.assertIn("Kalamazoo Valley CC RFP", budgeted)
        self.assertLessEqual(len(budgeted), 2_100)

    def test_empty_context_is_safe(self) -> None:
        self.assertEqual(_budget_rfp_context(""), "")

    def test_blocks_keep_their_assembly_order(self) -> None:
        budgeted = _budget_rfp_context(FULL_CONTEXT)
        self.assertLess(budgeted.index("HARD FACTS"), budgeted.index("Mapped section requirements"))
        self.assertLess(
            budgeted.index("Mapped section requirements"),
            budgeted.index("FULL PROPOSAL MANUSCRIPT"),
        )
        self.assertLess(budgeted.index("FULL PROPOSAL MANUSCRIPT"), budgeted.index("00_Guide_Pricing"))


if __name__ == "__main__":
    unittest.main()
