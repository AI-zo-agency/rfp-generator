"""All-in expenses vs guide reimbursable block + MANUAL FILL preserve."""

from __future__ import annotations

import unittest

from app.services.proposal_budget_content import (
    ensure_pricing_guide_verbatim_in_budget_markdown,
    manuscript_asserts_all_in_no_separate_expenses,
    strip_guide_reimbursable_expenses_heading_block,
)
from app.services.proposal_manual_flags import (
    mask_manual_fill_tags,
    missing_manual_fill_placeholders,
    unmask_manual_fill_tags,
)


class AllInExpensesTests(unittest.TestCase):
    def test_detects_no_separate_expense_assertion(self) -> None:
        body = (
            "No expense will appear on an invoice as a standalone charge. "
            "Fees below are all-in."
        )
        self.assertTrue(manuscript_asserts_all_in_no_separate_expenses(body))

    def test_strip_reimbursable_block(self) -> None:
        body = (
            "### Scope Protection\n\nKeep me.\n\n"
            "### Reimbursable Expenses\n\n"
            "The following expenses will be billed at cost with prior approval: travel "
            "(mileage at current IRS rate, lodging, meals); photography/videography "
            "location fees and permits; specialized software licenses required for "
            "project-specific needs; stock photography/video licensing beyond standard "
            "subscriptions.\n\n"
            "### Revision Rounds\n\nThree rounds.\n"
        )
        out = strip_guide_reimbursable_expenses_heading_block(body)
        self.assertNotIn("Reimbursable Expenses", out)
        self.assertNotIn("mileage at current IRS rate", out)
        self.assertIn("Scope Protection", out)
        self.assertIn("Revision Rounds", out)

    def test_ensure_verbatim_omits_reimbursable_when_all_in(self) -> None:
        body = (
            "## Terms\n\n"
            "No expense reimbursement will be billed separately from the fees below.\n\n"
            "### Reimbursable Expenses\n\n"
            "The following expenses will be billed at cost with prior approval: travel "
            "(mileage at current IRS rate, lodging, meals); photography/videography "
            "location fees and permits; specialized software licenses required for "
            "project-specific needs; stock photography/video licensing beyond standard "
            "subscriptions.\n"
        )
        out = ensure_pricing_guide_verbatim_in_budget_markdown(body)
        self.assertNotIn("### Reimbursable Expenses", out)
        self.assertNotIn("mileage at current irs rate", out.casefold())
        self.assertIn("Investment Framing", out)
        self.assertIn("all-in", out.casefold())

    def test_omit_reimbursable_action_from_demand(self) -> None:
        from app.services.rfp_cost_demands import (
            RfpCostDemand,
            demands_require_omit_guide_reimbursables,
        )

        self.assertFalse(
            demands_require_omit_guide_reimbursables(
                [RfpCostDemand(id="x", kind="disclosure", requirement="state commission")]
            )
        )
        self.assertTrue(
            demands_require_omit_guide_reimbursables(
                [
                    RfpCostDemand(
                        id="expenses_all_in",
                        kind="reimbursable",
                        requirement="Fees are all-in; no separate expense billing",
                        actions=["omit_guide_reimbursable_expenses"],
                    )
                ]
            )
        )

    def test_manual_fill_mask_roundtrip_detects_drop(self) -> None:
        prior = (
            "Total $98,125.\n"
            "[MANUAL FILL: Sonja — confirm $98,125 as binding NTE ceiling]\n"
        )
        masked, originals = mask_manual_fill_tags(prior)
        self.assertIn("«MFILL_0»", masked)
        fake = "Total $98,125. Confirmed as binding NTE."
        dropped = missing_manual_fill_placeholders(fake, originals)
        self.assertEqual(len(dropped), 1)
        restored = unmask_manual_fill_tags(masked, originals)
        self.assertIn("MANUAL FILL: Sonja", restored)


if __name__ == "__main__":
    unittest.main()
