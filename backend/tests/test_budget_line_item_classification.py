"""One travel row must be counted once.

Observed: a single $3,500 travel line was classified as agency_fee by
infer_line_item_type and as reimbursables by _professional_fees_and_direct,
making fee == travel == total while every invariant passed.
"""

from __future__ import annotations

import itertools
import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_content import (
    _professional_fees_and_direct,
    canonical_budget_summary_figures,
    reconcile_budget_summary_prose,
)
from app.services.proposal_budget_validation import (
    direct_expense_subtotal,
    infer_line_item_type,
    reconcile_proposal_budget,
    scale_line_items_to_hard_cap,
    split_line_item_totals,
    validate_budget_canonical,
)

# BudgetLineItem requires `id` and `category` (app/models/proposal.py:72-73) but
# the brief's fixture only passed description/extended/kw. This counter/default
# only satisfies pydantic's required fields — it does not touch any assertion.
_ids = itertools.count()


def _item(description: str, extended: float, **kw) -> BudgetLineItem:
    kw.setdefault("id", f"li-{next(_ids)}")
    kw.setdefault("category", "")
    return BudgetLineItem(description=description, extended=extended, **kw)


class ClassificationTests(unittest.TestCase):
    def test_travel_is_a_direct_expense(self) -> None:
        self.assertEqual(
            infer_line_item_type(_item("Travel — on-site listening sessions", 3500)),
            "direct_expense",
        )

    def test_all_travel_vocabulary_classifies_as_direct(self) -> None:
        for text in ("airfare", "lodging", "per diem", "mileage", "hotel", "ground transport"):
            with self.subTest(text=text):
                self.assertEqual(infer_line_item_type(_item(f"Estimated {text}", 100)), "direct_expense")

    def test_explicit_line_item_type_still_wins(self) -> None:
        item = _item("Travel", 3500, lineItemType="agency_fee")
        self.assertEqual(infer_line_item_type(item), "agency_fee")

    def test_strategy_work_is_still_agency_fee(self) -> None:
        self.assertEqual(infer_line_item_type(_item("Strategy & creative foundation", 14000)), "agency_fee")

    def test_media_is_still_passthrough(self) -> None:
        self.assertEqual(infer_line_item_type(_item("Client media pass-through", 50000)), "client_passthrough")

    def test_agency_fee_subtotal_excludes_travel(self) -> None:
        items = [_item("Strategy", 14000), _item("Travel — site visits", 3500)]
        line_sum, agency_fee, passthrough = split_line_item_totals(items)
        self.assertEqual(agency_fee, 14000.0)
        self.assertEqual(passthrough, 0.0)
        self.assertEqual(line_sum, 17500.0)
        self.assertEqual(direct_expense_subtotal(items), 3500.0)

    def test_line_sum_still_covers_every_bucket(self) -> None:
        items = [_item("Strategy", 14000), _item("Travel", 3500), _item("Client media spend", 50000)]
        line_sum, agency_fee, passthrough = split_line_item_totals(items)
        self.assertEqual(line_sum, round(agency_fee + passthrough + direct_expense_subtotal(items), 2))

    def test_the_observed_all_travel_budget_no_longer_looks_like_fees(self) -> None:
        """The $3,500 case: one travel row, nothing else."""
        items = [_item("Travel — on-site listening sessions", 3500, category="travel")]
        _line_sum, agency_fee, _passthrough = split_line_item_totals(items)
        self.assertEqual(agency_fee, 0.0, "travel must not become an agency fee")

    def test_fees_and_direct_agree_with_the_classifier(self) -> None:
        """_professional_fees_and_direct and split_line_item_totals must not disagree."""
        from app.models.proposal import ProposalBudget

        items = [_item("Strategy", 14000), _item("Travel — site visits", 3500)]
        budget = ProposalBudget(
            rfpId="r1", updatedAt="t", lineItems=items, directExpensesTotal=0.0
        )
        fees, reimbursables = _professional_fees_and_direct(budget)
        _line_sum, agency_fee, _pt = split_line_item_totals(items)
        self.assertEqual(fees, agency_fee)
        self.assertEqual(reimbursables, direct_expense_subtotal(items))


def _budget(items: list[BudgetLineItem], **kw) -> ProposalBudget:
    return ProposalBudget(rfpId="r1", updatedAt="t", lineItems=items, **kw)


class ReconcileDoesNotRelabelTravelAsFeeTests(unittest.TestCase):
    """The $3,500 defect end-to-end, not just at the classifier.

    split_line_item_totals returning agency_fee=0 is not enough: reconcile
    overwrote it with line_sum (which includes travel) on the non-commission
    path, and every downstream consumer reads the stored agencyFeeSubtotal.
    """

    def test_all_travel_budget_stores_zero_agency_fee(self) -> None:
        reconciled = reconcile_proposal_budget(
            _budget([_item("Travel — on-site listening sessions", 3500, category="travel")])
        )
        self.assertEqual(reconciled.agency_fee_subtotal, 0.0, "travel is not an agency fee")
        self.assertEqual(reconciled.agency_revenue_estimate, 3500.0)
        self.assertEqual(reconciled.line_item_sum, 3500.0)
        self.assertEqual(validate_budget_canonical(reconciled), [])

    def test_mixed_budget_stores_fees_without_travel(self) -> None:
        reconciled = reconcile_proposal_budget(
            _budget(
                [
                    _item("Strategy & creative foundation", 14000),
                    _item("Travel — site visits", 3500),
                ]
            )
        )
        self.assertEqual(reconciled.agency_fee_subtotal, 14000.0)
        self.assertEqual(reconciled.agency_revenue_estimate, 17500.0)
        self.assertEqual(reconciled.line_item_sum, 17500.0)
        self.assertEqual(validate_budget_canonical(reconciled), [])

    def test_client_figures_do_not_report_travel_as_fee(self) -> None:
        reconciled = reconcile_proposal_budget(
            _budget([_item("Travel — on-site listening sessions", 3500, category="travel")])
        )
        figs = canonical_budget_summary_figures(reconciled)
        self.assertEqual(figs["agency_fee"], 0.0)
        self.assertEqual(figs["direct"], 3500.0)
        self.assertEqual(figs["total"], 3500.0)

    def test_client_prose_does_not_call_travel_an_agency_fee(self) -> None:
        """fee == travel == total in one rendered sentence is the whole defect."""
        reconciled = reconcile_proposal_budget(
            _budget([_item("Travel — on-site listening sessions", 3500, category="travel")])
        )
        content = (
            "Total Year 1 agency fee: $1. "
            "Client media pass-through billed at net: $1. "
            "Direct travel/reimbursables: $1. "
            "Total Year 1 client invoicing: $1."
        )
        out, _n = reconcile_budget_summary_prose(content, reconciled)
        self.assertIn("Direct travel/reimbursables: $3,500", out)
        self.assertNotIn("agency fee: $3,500", out)

    def test_manuscript_sync_slots_do_not_report_travel_as_fee(self) -> None:
        """proposal_budget_sync carried the same agency_revenue fallback."""
        from app.services.proposal_budget_sync import _canonical_slot_values

        reconciled = reconcile_proposal_budget(
            _budget([_item("Travel — on-site listening sessions", 3500, category="travel")])
        )
        slots = _canonical_slot_values(reconciled)
        self.assertEqual(slots["agency_fee"], 0.0)
        self.assertEqual(slots["direct_expenses"], 3500.0)
        self.assertEqual(slots["total_invoicing"], 3500.0)


class HardCapDoesNotScaleTravelTests(unittest.TestCase):
    """Travel is billed at cost — scaling it to fit a fee ceiling invents a discount."""

    def test_travel_is_reserved_from_the_cap_and_left_at_cost(self) -> None:
        scaled, notes = scale_line_items_to_hard_cap(
            [
                _item("Strategy and creative delivery", 190_000),
                _item("Travel — site visits", 10_000),
            ],
            hard_cap=100_000.0,
            direct_expenses=0.0,
        )
        self.assertTrue(notes)
        by_desc = {i.description: round(float(i.extended or 0), 2) for i in scaled}
        self.assertEqual(by_desc["Travel — site visits"], 10_000.0, "travel must not be scaled")
        self.assertEqual(by_desc["Strategy and creative delivery"], 90_000.0)
        # The cap is spent exactly, not under-spent by a silently discounted travel row.
        self.assertEqual(round(sum(by_desc.values()), 2), 100_000.0)


class DedupeUsesTheSharedClassifierTests(unittest.TestCase):
    """dedupe_travel_vs_direct_expenses must see every row infer_line_item_type calls direct."""

    def test_reimbursable_wording_clears_the_duplicate_direct_bucket(self) -> None:
        reconciled = reconcile_proposal_budget(
            _budget(
                [
                    _item("Strategy", 14000),
                    _item("Reimbursable out-of-pocket expenses", 3500),
                ],
                directExpensesTotal=3500,
            )
        )
        self.assertEqual(
            reconciled.agency_revenue_estimate,
            17500.0,
            "the same $3,500 was counted in a line item and in directExpensesTotal",
        )
        self.assertEqual(reconciled.agency_fee_subtotal, 14000.0)
        self.assertEqual(validate_budget_canonical(reconciled), [])


if __name__ == "__main__":
    unittest.main()
