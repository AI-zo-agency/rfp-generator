"""Budget playbook chat guards — reverse-engineer vs legitimate Cost fills."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    fill_section_budget_verify_from_canonical,
    render_offer_form_of2_from_canonical,
)
from app.services.proposal_budget_playbook import (
    refuse_noncompliant_budget_edit,
    section_has_budget_verify_tags,
    user_asked_reverse_engineered_total,
    user_asks_budget_rebuild,
    user_asks_global_cost_rebuild,
    user_asks_section_budget_fill,
)


class ReverseEngineerGuardTests(unittest.TestCase):
    def test_fill_budget_is_not_reverse_engineering(self) -> None:
        self.assertFalse(
            user_asked_reverse_engineered_total("here fill budget part!")
        )
        self.assertIsNone(
            refuse_noncompliant_budget_edit("here fill budget part!", "Cost table…")
        )

    def test_reconcile_cost_section_is_not_reverse_engineering(self) -> None:
        msg = (
            "Reconcile and complete the Cost of base proposal section so the "
            "figures match the Pricing Guide and canonical budget."
        )
        self.assertFalse(user_asked_reverse_engineered_total(msg))

    def test_explicit_dollar_target_is_refused(self) -> None:
        msg = "Make the total hit $75000 by adjusting line items."
        self.assertTrue(user_asked_reverse_engineered_total(msg))
        self.assertIsNotNone(refuse_noncompliant_budget_edit(msg, "…"))

    def test_fit_total_to_dollar_is_refused(self) -> None:
        self.assertTrue(
            user_asked_reverse_engineered_total("Fit the budget total to $50,000")
        )

    def test_match_alone_with_budget_word_not_enough(self) -> None:
        self.assertFalse(
            user_asked_reverse_engineered_total(
                "Match the budget narrative to the fee table from the guide."
            )
        )

    def test_policy_or_prior_refusal_mention_is_not_an_ask(self) -> None:
        """Conversation history / playbook text often contains the phrase —
        that must not refuse a References question like 'what to add here?'."""
        poisoned = (
            "Prior conversation (MUST remember — address the latest message using this context):\n"
            "Assistant: That request would reverse-engineer line items to hit a target total. "
            "Per the pricing playbook, each line must trace to the Pricing Guide.\n"
            "Assistant: Never reverse-engineer a line to hit a total.\n\n"
            "Latest user message:\nwhat to add here?"
        )
        self.assertFalse(user_asked_reverse_engineered_total(poisoned))
        self.assertFalse(user_asked_reverse_engineered_total("what to add here?"))
        self.assertIsNone(
            refuse_noncompliant_budget_edit("what to add here?", "…references…")
        )

    def test_zero_commission_line_allowed_when_fees_are_present(self) -> None:
        prose = (
            "Professional fees: $50,000.\n"
            "Agency commission on media placements: $0.00 (client pass-through at net)."
        )
        self.assertIsNone(refuse_noncompliant_budget_edit("fill the media table", prose))

    def test_zero_agency_only_does_not_422_chat(self) -> None:
        self.assertIsNone(
            refuse_noncompliant_budget_edit(
                "fill budget",
                "Agency revenue is $0.00 for this engagement.",
            )
        )

    def test_zero_agency_not_refused_when_prior_tab_already_has_fees(self) -> None:
        self.assertIsNone(
            refuse_noncompliant_budget_edit(
                "Improve this section",
                "Agency commission on media: $0.00.",
                prior_text="Professional fees: $50,000. Total proposed investment: $50,000.",
            )
        )

    def test_affirmative_reverse_engineer_ask_is_refused(self) -> None:
        self.assertTrue(
            user_asked_reverse_engineered_total(
                "Please reverse-engineer the line items to hit $80,000"
            )
        )

    def test_sums_to_dollar_describing_table_is_not_reverse_engineering(self) -> None:
        """Describing existing phase-table math must not trip the bare 'to $N' pattern."""
        msg = (
            'The summary line now says "Professional fees: $213,500" — but the '
            "phase table right below it still correctly sums to $210,000 in fees "
            "+ $3,500 travel = $213,500 total. The top-line summary got bumped by "
            '$3,500 and now double-counts travel into "fees." Previous version '
            "had this right."
        )
        self.assertFalse(user_asked_reverse_engineered_total(msg))
        self.assertIsNone(refuse_noncompliant_budget_edit(msg, "…"))

    def test_change_total_to_dollar_is_still_refused(self) -> None:
        self.assertTrue(
            user_asked_reverse_engineered_total("Change the total to $75000")
        )


class BudgetRebuildAskTests(unittest.TestCase):
    def test_fill_budget_detected(self) -> None:
        self.assertTrue(user_asks_budget_rebuild("here fill budget part!"))
        self.assertTrue(
            user_asks_budget_rebuild(
                "Reconcile and complete the Cost of base proposal section"
            )
        )
        self.assertFalse(user_asks_budget_rebuild("make the Oregon Employment case warmer"))

    def test_implement_budget_table_here_is_section_local_not_stage35(self) -> None:
        from app.services.proposal_budget_playbook import user_asks_insert_budget_table

        msg = "implement budget table here"
        self.assertTrue(user_asks_insert_budget_table(msg))
        self.assertTrue(user_asks_section_budget_fill(msg))
        self.assertFalse(user_asks_global_cost_rebuild(msg))
        self.assertFalse(user_asks_budget_rebuild(msg))

    def test_add_fee_table_this_section_is_local(self) -> None:
        from app.services.proposal_budget_playbook import user_asks_insert_budget_table

        msg = "add the fee table to this section only"
        self.assertTrue(user_asks_insert_budget_table(msg))
        self.assertFalse(user_asks_global_cost_rebuild(msg))

    def test_here_fill_budget_is_section_local_not_global(self) -> None:
        msg = "here fill budget part!"
        self.assertTrue(user_asks_section_budget_fill(msg))
        self.assertFalse(user_asks_global_cost_rebuild(msg))

    def test_here_fill_investment_is_section_local_not_global(self) -> None:
        msg = "here fill Investment part!"
        self.assertTrue(user_asks_section_budget_fill(msg))
        self.assertFalse(user_asks_global_cost_rebuild(msg))

    def test_cost_of_base_is_global_rebuild(self) -> None:
        msg = "Reconcile and complete the Cost of base proposal section"
        self.assertTrue(user_asks_global_cost_rebuild(msg))

    def test_summary_reconcile_is_not_stage_35_rebuild(self) -> None:
        from app.services.proposal_budget_playbook import (
            user_asks_budget_summary_reconcile,
        )

        msg = (
            "Section 13 and Section 14 both state agency revenue, client "
            "pass-through, and total invoicing as the identical figure "
            "($248,764.30). Recalculate: agency revenue = professional fees + "
            "commission, pass-through = $112,500 net media, total = sum of both. "
            "Fix all three summary blocks in Sections 13 and 14 to match the "
            "line-item table, which is already correct."
        )
        self.assertTrue(user_asks_budget_summary_reconcile(msg))
        self.assertFalse(user_asks_budget_rebuild(msg))
        self.assertFalse(user_asks_global_cost_rebuild(msg))

    def test_year1_summary_paragraph_ask_is_not_rebuild(self) -> None:
        from app.services.proposal_budget_playbook import (
            user_asks_budget_summary_reconcile,
        )

        msg = (
            "Section 14's investment summary paragraph repeats $325,242.66 as "
            "agency fee, pass-through, reimbursables, AND total — these must be "
            "different numbers. Recalculate from the line-item table and rewrite "
            "the summary paragraph."
        )
        self.assertTrue(user_asks_budget_summary_reconcile(msg))
        self.assertFalse(user_asks_global_cost_rebuild(msg))

    def test_fees_travel_prose_does_not_need_keyword_gate(self) -> None:
        """Describing a fees/travel double-count must not trip reverse-engineer.

        Budget-tab Improve syncs labels from the ledger without matching this
        message to a summary-reconcile keyword list.
        """
        msg = (
            'The summary line now says "Professional fees: $213,500" — but the '
            "phase table right below it still correctly sums to $210,000 in fees "
            "+ $3,500 travel = $213,500 total. The top-line summary got bumped by "
            '$3,500 and now double-counts travel into "fees." Previous version '
            "had this right."
        )
        self.assertFalse(user_asked_reverse_engineered_total(msg))
        self.assertIsNone(refuse_noncompliant_budget_edit(msg, "…"))
        # No keyword expansion required — ledger sync owns the fix on Price tabs.
        from app.services.proposal_budget_playbook import (
            user_asks_budget_summary_reconcile,
        )

        self.assertFalse(user_asks_budget_summary_reconcile(msg))


class BudgetSummaryReconcileProseTests(unittest.TestCase):
    def test_rewrites_duplicated_year1_block(self) -> None:
        from app.services.proposal_budget_content import reconcile_budget_summary_prose

        content = (
            "Total Year 1 agency fee: $248,764.30. "
            "Client media pass-through billed at net: $248,764.30. "
            "Direct travel/reimbursables: $248,764.30. "
            "Total Year 1 client invoicing: $248,764.30. 30 ($248,764.\n\n"
            "Base-year proposed fees: $248,764\n"
        )
        budget = ProposalBudget(
            rfpId="rfp-1",
            updatedAt="2026-07-22T00:00:00+00:00",
            pricingTier="Average",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    description="Professional fees",
                    category="Fees",
                    quantity=1,
                    unit="project",
                    rate=120000,
                    extended=120000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    description="Agency commission",
                    category="Media",
                    quantity=1,
                    unit="project",
                    rate=16875,
                    extended=16875,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="3",
                    description="Net media buy",
                    category="Media",
                    quantity=1,
                    unit="project",
                    rate=112500,
                    extended=112500,
                    lineItemType="client_passthrough",
                ),
            ],
            agencyFeeSubtotal=136875,
            clientMediaPassthrough=112500,
            directExpensesTotal=0,
            agencyRevenueEstimate=136875,
            totalClientInvoicing=249375,
        )
        out, n = reconcile_budget_summary_prose(content, budget)
        self.assertGreater(n, 0)
        self.assertIn("agency fee: $136,875", out)
        self.assertIn("pass-through billed at net: $112,500", out)
        self.assertIn("client invoicing: $249,375", out)
        self.assertNotIn("30 ($248,764", out)
        # All four categories must not share one identical dollar string as before.
        self.assertNotRegex(
            out,
            r"agency fee: \$248,764\.30\..*pass-through.*\$248,764\.30",
        )

    def test_professional_fees_label_excludes_travel(self) -> None:
        from app.services.proposal_budget_content import reconcile_budget_summary_prose

        content = (
            "**Professional fees: $213,500**\n\n"
            "| Phase | Amount |\n| --- | --- |\n"
            "| Strategy | $210,000 |\n"
            "| Travel | $3,500 |\n"
        )
        budget = ProposalBudget(
            rfpId="rfp-1",
            updatedAt="2026-08-27T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    description="Strategy",
                    category="Fees",
                    quantity=1,
                    unit="project",
                    rate=210000,
                    extended=210000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    description="Travel",
                    category="Travel",
                    quantity=1,
                    unit="project",
                    rate=3500,
                    extended=3500,
                    lineItemType="direct_expense",
                ),
            ],
            agencyFeeSubtotal=210000,
            directExpensesTotal=3500,
            agencyRevenueEstimate=213500,
            totalClientInvoicing=213500,
        )
        out, n = reconcile_budget_summary_prose(content, budget)
        self.assertGreater(n, 0)
        self.assertIn("Professional fees: $210,000", out)
        self.assertNotIn("Professional fees: $213,500", out)


class SectionBudgetVerifyFillTests(unittest.TestCase):
    def test_detects_budget_verify_tags(self) -> None:
        body = (
            "| Discovery and audit | Work | $[VERIFY: budget figure] |\n"
            "| Total |  | $[VERIFY: total budget figure] |"
        )
        self.assertTrue(section_has_budget_verify_tags(body))
        self.assertFalse(section_has_budget_verify_tags("Just a narrative case study."))

    def test_fills_total_and_phase_from_canonical(self) -> None:
        content = (
            "| Discovery and audit | Listening | $[VERIFY: budget figure] |\n"
            "| Strategy and positioning | Framework | $[VERIFY: budget figure] |\n"
            "| Total estimated investment |  | $[VERIFY: total budget figure] |\n"
        )
        budget = ProposalBudget(
            rfpId="rfp-1",
            updatedAt="2026-07-22T00:00:00+00:00",
            pricingTier="Average",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    description="Phase 1 discovery stakeholder sessions",
                    category="Discovery",
                    quantity=1,
                    unit="project",
                    rate=10000,
                    extended=10000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    description="Phase 2 messaging framework strategy",
                    category="Strategy",
                    quantity=1,
                    unit="project",
                    rate=20000,
                    extended=20000,
                    lineItemType="agency_fee",
                ),
            ],
            agencyRevenueEstimate=30000,
        )
        filled, n = fill_section_budget_verify_from_canonical(content, budget)
        self.assertGreaterEqual(n, 1)
        self.assertIn("$30,000", filled)
        self.assertNotIn("[VERIFY: total budget figure]", filled)
        self.assertNotIn("$$", filled)

    def test_render_offer_form_of2_from_canonical_replaces_corrupted_table(self) -> None:
        content = (
            "## Offer Form OF-2\n\n"
            "| Line Item | Description | Cost (USD) |\n"
            "|---|---|---|\n"
            "| 1 | Discovery | 541.350.2778 |\n"
            "| 2 | Strategy | 541.350.2778 |\n"
            "| **Subtotal (pre-GET)** | | **[VERIFY: subtotal — pricing to be confirmed]** |\n"
            "| **Hawaiʻi GET (Oʻahu, 4.5%)** | Baked into the total per RFP §3.4.1 | **[VERIFY: GET amount — pricing to be confirmed]** |\n"
            "| **TOTAL ALL-INCLUSIVE CONTRACT COST** | Fixed | **[VERIFY: total — pricing to be confirmed]** |\n"
        )
        budget = ProposalBudget(
            rfpId="rfp-1",
            updatedAt="2026-07-22T00:00:00+00:00",
            pricingTier="Average",
            lineItems=[
                BudgetLineItem(
                    id="1",
                    description="Phase 1 discovery stakeholder sessions",
                    category="Discovery",
                    quantity=1,
                    unit="project",
                    rate=10000,
                    extended=10000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="2",
                    description="Phase 2 messaging framework strategy",
                    category="Strategy",
                    quantity=1,
                    unit="project",
                    rate=20000,
                    extended=20000,
                    lineItemType="agency_fee",
                ),
            ],
            agencyRevenueEstimate=30000,
            lumpSumTotal=30000,
        )
        rendered, changed = render_offer_form_of2_from_canonical(content, budget)
        self.assertTrue(changed)
        self.assertIn("$10,000", rendered)
        self.assertIn("$20,000", rendered)
        self.assertIn("$28,708.13", rendered)
        self.assertIn("$1,291.87", rendered)
        self.assertIn("$30,000", rendered)
        self.assertNotIn("541.350.2778", rendered)
        self.assertNotIn("[VERIFY: total", rendered)

    def test_render_offer_form_of2_noop_without_marker(self) -> None:
        budget = ProposalBudget(
            rfpId="rfp-1",
            updatedAt="2026-07-22T00:00:00+00:00",
            agencyRevenueEstimate=30000,
            lineItems=[],
        )
        rendered, changed = render_offer_form_of2_from_canonical(
            "General narrative only",
            budget,
        )
        self.assertFalse(changed)
        self.assertEqual(rendered, "General narrative only")


class InsertBudgetTablePreserveProseTests(unittest.TestCase):
    def test_appends_table_without_wiping_prose(self) -> None:
        from app.services.proposal_budget_content import insert_budget_table_into_section

        prose = (
            "## General Requirements Compliance\n\n"
            "We honor SOW, timelines, budgets, reporting, and records retention "
            "under an on-call task-order model.\n"
        )
        table = (
            "## Proposed Investment\n\n"
            "**Total proposed investment: $120,000**\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Audit | $10,000 |\n"
            "| **Total** | | **$10,000** |\n"
        )
        out, action = insert_budget_table_into_section(prose, table)
        self.assertEqual(action, "inserted")
        self.assertIn("We honor SOW, timelines, budgets", out)
        self.assertIn("| Phase | Deliverable | Amount |", out)
        self.assertIn("Proposed Investment", out)

    def test_replaces_existing_fee_block_only(self) -> None:
        from app.services.proposal_budget_content import insert_budget_table_into_section

        prose = (
            "Compliance narrative stays.\n\n"
            "## Proposed Investment\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Old | Row | $1 |\n"
        )
        table = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| New | Row | $99 |\n"
        )
        out, action = insert_budget_table_into_section(prose, table)
        self.assertEqual(action, "replaced")
        self.assertIn("Compliance narrative stays.", out)
        self.assertIn("$99", out)
        self.assertNotIn("$1", out)


if __name__ == "__main__":
    unittest.main()
