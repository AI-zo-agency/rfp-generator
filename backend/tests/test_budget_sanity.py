"""Deterministic budget sanity checks — reproduces the live $2,200 defect.

The real ProposalBudget shipped: pricing_tier=Average, lump_sum_total=2200,
agency_fee_subtotal=2200, no rfp_budget_cap/floor/envelope, and two line
items — one priced ("Group 1 Media Planning & Advertising — Digital...",
qty=1, rate=2200, extended=2200) and one completely empty ("Group 1 Media
Planning & Advertising — Monthly...", qty=None, rate=None, extended=None).
Existing guards could not catch it because collect_under_minimum_flags needs
rfp_budget_floor, and this RFP stated no budget. This is a pure unit test —
no network/LLM calls.
"""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget, RfpSectionMap
from app.services.proposal_budget_sanity import (
    collect_budget_sanity_flags,
    collect_budget_scope_gap_flags,
    budget_scope_group_coverage,
)


def _line(
    id_: str,
    description: str,
    *,
    quantity: float | None = None,
    rate: float | None = None,
    extended: float | None = None,
    category: str = "Media",
) -> BudgetLineItem:
    return BudgetLineItem(
        id=id_,
        category=category,
        description=description,
        quantity=quantity,
        rate=rate,
        extended=extended,
    )


def _budget(line_items: list[BudgetLineItem], **overrides) -> ProposalBudget:
    kwargs = dict(
        rfpId="rfp-budget-sanity-test",
        updatedAt="2026-09-03T00:00:00+00:00",
        lineItems=line_items,
    )
    kwargs.update(overrides)
    return ProposalBudget(**kwargs)


class BudgetSanityFlagTests(unittest.TestCase):
    def test_live_defect_fires_empty_line_and_single_line_total(self) -> None:
        budget = _budget(
            [
                _line(
                    "L01",
                    "Group 1 Media Planning & Advertising — Digital campaign management",
                    quantity=1.0,
                    rate=2200.0,
                    extended=2200.0,
                ),
                _line(
                    "L02",
                    "Group 1 Media Planning & Advertising — Monthly reporting",
                    quantity=None,
                    rate=None,
                    extended=None,
                ),
            ],
            lumpSumTotal=2200.0,
            agencyFeeSubtotal=2200.0,
            pricingTier="Average",
        )

        flags = collect_budget_sanity_flags(budget)

        self.assertTrue(
            any("Empty priced line" in f and "Monthly reporting" in f for f in flags),
            flags,
        )
        self.assertTrue(
            any("Single-line total" in f for f in flags),
            flags,
        )

    def test_healthy_multi_line_budget_produces_no_flags(self) -> None:
        budget = _budget(
            [
                _line("L01", "Strategy & planning", quantity=1.0, rate=1000.0, extended=1000.0),
                _line("L02", "Creative production", quantity=1.0, rate=1500.0, extended=1500.0),
                _line("L03", "Media buying", quantity=1.0, rate=1700.0, extended=1700.0),
            ],
            lumpSumTotal=4200.0,
        )

        flags = collect_budget_sanity_flags(budget)

        self.assertEqual(flags, [])

    def test_all_unpriced_budget_produces_no_flags(self) -> None:
        """A not-yet-priced budget is not a contradiction — must return nothing."""
        budget = _budget(
            [
                _line("L01", "Strategy & planning"),
                _line("L02", "Creative production"),
            ],
        )

        flags = collect_budget_sanity_flags(budget)

        self.assertEqual(flags, [])

    def test_total_mismatch_fires_exactly_the_mismatch_flag(self) -> None:
        budget = _budget(
            [
                _line("L01", "Strategy & planning", quantity=1.0, rate=2000.0, extended=2000.0),
                _line("L02", "Creative production", quantity=1.0, rate=3000.0, extended=3000.0),
            ],
            lumpSumTotal=2200.0,
        )

        flags = collect_budget_sanity_flags(budget)

        self.assertEqual(len(flags), 1)
        self.assertIn("Total mismatch", flags[0])
        self.assertIn("5,000.00", flags[0])
        self.assertIn("2,200.00", flags[0])

    def test_none_and_garbage_input_never_raises(self) -> None:
        self.assertEqual(collect_budget_sanity_flags(None), [])
        self.assertEqual(collect_budget_sanity_flags("not a budget"), [])
        self.assertEqual(collect_budget_sanity_flags(object()), [])
        self.assertEqual(collect_budget_sanity_flags(42), [])


class BudgetScopeGapTests(unittest.TestCase):
    def _sections(self, titles: list[str]) -> list[RfpSectionMap]:
        return [
            RfpSectionMap(id=f"s{i}", title=title) for i, title in enumerate(titles)
        ]

    def test_only_one_group_priced_flags_gap(self) -> None:
        sections = self._sections(
            ["Group 1 — Digital Marketing", "Group 2 — Print", "Group 3 — Events"]
        )
        budget = _budget(
            [
                _line("L01", "Group 1 media planning", quantity=1.0, rate=1000.0, extended=1000.0),
            ],
            lumpSumTotal=1000.0,
        )

        found, all_groups = budget_scope_group_coverage(budget, sections)
        self.assertEqual(found, {"Group 1"})
        self.assertEqual(all_groups, {"Group 1", "Group 2", "Group 3"})

        flags = collect_budget_scope_gap_flags(budget, sections)
        self.assertEqual(len(flags), 1)
        self.assertIn("only 1 of the RFP's 3 scope groups", flags[0])

    def test_all_groups_priced_no_flag(self) -> None:
        sections = self._sections(
            ["Group 1 — Digital Marketing", "Group 2 — Print", "Group 3 — Events"]
        )
        budget = _budget(
            [
                _line("L01", "Group 1 media planning", quantity=1.0, rate=1000.0, extended=1000.0),
                _line("L02", "Group 2 print production", quantity=1.0, rate=800.0, extended=800.0),
                _line("L03", "Group 3 event support", quantity=1.0, rate=600.0, extended=600.0),
            ],
            lumpSumTotal=2400.0,
        )

        flags = collect_budget_scope_gap_flags(budget, sections)
        self.assertEqual(flags, [])

    def test_unrelated_titles_not_confident_no_flag(self) -> None:
        sections = self._sections(
            ["Executive Summary", "Scope of Work", "Pricing", "References"]
        )
        budget = _budget(
            [
                _line("L01", "Some deliverable", quantity=1.0, rate=1000.0, extended=1000.0),
            ],
            lumpSumTotal=1000.0,
        )

        found, all_groups = budget_scope_group_coverage(budget, sections)
        self.assertEqual(found, set())
        self.assertEqual(all_groups, set())

        flags = collect_budget_scope_gap_flags(budget, sections)
        self.assertEqual(flags, [])

    def test_scope_functions_never_raise_on_garbage(self) -> None:
        self.assertEqual(collect_budget_scope_gap_flags(None, None), [])
        self.assertEqual(budget_scope_group_coverage(None, None), (set(), set()))
        self.assertEqual(collect_budget_scope_gap_flags("x", 42), [])


if __name__ == "__main__":
    unittest.main()
