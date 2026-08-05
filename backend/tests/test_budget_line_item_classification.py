"""One travel row must be counted once.

Observed: a single $3,500 travel line was classified as agency_fee by
infer_line_item_type and as reimbursables by _professional_fees_and_direct,
making fee == travel == total while every invariant passed.
"""

from __future__ import annotations

import itertools
import unittest

from app.models.proposal import BudgetLineItem
from app.services.proposal_budget_content import _professional_fees_and_direct
from app.services.proposal_budget_validation import (
    direct_expense_subtotal,
    infer_line_item_type,
    split_line_item_totals,
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


if __name__ == "__main__":
    unittest.main()
