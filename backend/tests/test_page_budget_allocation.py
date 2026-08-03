"""Document-level page budget must bind at generation, not just at review.

Real symptom: an RFP capping the quote at 12 pages produced a manuscript of
~2,400 lines. Each section took its natural word target independently, so
~21 sections x 800 words = ~16,800 words against a 4,200-word cap — and the
overshoot was only reported by the presubmit reviewer, after every section had
already been paid for.
"""

from __future__ import annotations

import unittest

from app.services.proposal_drafting_graph import (
    ABSOLUTE_MIN_SECTION_WORDS,
    DEFAULT_WORD_TARGET,
    MIN_SECTION_WORDS,
    WORDS_PER_PAGE,
    allocate_word_budget,
)


class AllocateWordBudgetTests(unittest.TestCase):
    def test_twelve_page_rfp_with_21_sections_fits(self) -> None:
        budget = 12 * WORDS_PER_PAGE  # 4200
        natural = [DEFAULT_WORD_TARGET] * 21  # 16800 — 4x over

        allocated = allocate_word_budget(natural, budget)

        self.assertLessEqual(sum(allocated), budget)
        self.assertEqual(len(allocated), 21)
        self.assertTrue(all(w >= MIN_SECTION_WORDS for w in allocated))

    def test_no_budget_leaves_targets_untouched(self) -> None:
        natural = [800, 600, 400]
        self.assertEqual(allocate_word_budget(natural, None), natural)
        self.assertEqual(allocate_word_budget(natural, 0), natural)

    def test_targets_that_already_fit_are_untouched(self) -> None:
        natural = [800, 600, 400]  # 1800
        self.assertEqual(allocate_word_budget(natural, 5000), natural)

    def test_relative_emphasis_is_preserved(self) -> None:
        # A section with twice the natural target keeps more of the budget.
        natural = [1200, 600, 600]
        allocated = allocate_word_budget(natural, 1200)

        self.assertLessEqual(sum(allocated), 1200)
        self.assertGreater(allocated[0], allocated[1])
        self.assertEqual(allocated[1], allocated[2])

    def test_oversized_outline_holds_the_hard_floor_rather_than_stubbing(self) -> None:
        # 10 sections into 500 words: not solvable by shrinking. Sections stay
        # at the hard floor and the total intentionally exceeds budget — the
        # signal is "cut sections", not "emit 50-word stubs".
        allocated = allocate_word_budget([800] * 10, 500)

        self.assertEqual(len(allocated), 10)
        self.assertTrue(
            all(w >= ABSOLUTE_MIN_SECTION_WORDS for w in allocated), allocated
        )

    def test_empty_section_list(self) -> None:
        self.assertEqual(allocate_word_budget([], 4200), [])

    def test_allocation_fits_budget_whenever_the_outline_can_fit(self) -> None:
        for count in (1, 3, 8, 21, 40):
            for pages in (2, 5, 12, 30):
                budget = pages * WORDS_PER_PAGE
                natural_total = DEFAULT_WORD_TARGET * count
                if natural_total <= budget:
                    continue
                if count * ABSOLUTE_MIN_SECTION_WORDS > budget:
                    continue  # outline cannot fit at any length; see test above
                allocated = allocate_word_budget([DEFAULT_WORD_TARGET] * count, budget)
                self.assertLessEqual(
                    sum(allocated),
                    budget + count,  # rounding slack of <1 word per section
                    msg=f"count={count} pages={pages} allocated={sum(allocated)}",
                )


if __name__ == "__main__":
    unittest.main()
