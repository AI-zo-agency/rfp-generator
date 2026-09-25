"""The investment sentence must stay internally consistent across rewrites."""

from __future__ import annotations

import re
import unittest

from app.services.proposal_budget_content import _rewrite_investment_sentence


class RewriteInvestmentSentenceCentsTests(unittest.TestCase):
    """Cents amounts must not be truncated at the decimal point."""

    def test_cents_total_rewritten_once_not_tripled(self) -> None:
        # Observed DuPage-style bug: strip stopped at $154,026. → left "32 (…)"
        # then re-appended the full clause on every prepare/render pass.
        fees, direct, total = 150_526.32, 3_500.0, 154_026.32
        once = _rewrite_investment_sentence("", fees, direct, total)
        twice = _rewrite_investment_sentence(once, fees, direct, total)
        thrice = _rewrite_investment_sentence(twice, fees, direct, total)
        expected = (
            "Total proposed investment: $154,026.32 "
            "($150,526.32 in professional fees plus $3,500 in direct travel expenses)."
        )
        self.assertEqual(once, expected)
        self.assertEqual(twice, expected)
        self.assertEqual(thrice, expected)
        self.assertEqual(thrice.count("in professional fees"), 1)
        self.assertEqual(thrice.count("("), 1)
        # Orphan cents remnant — not the ".32 (" inside $154,026.32 (
        self.assertIsNone(re.search(r"(?<![\d.])\d{1,3}\s*\(\$", thrice))

    def test_clears_already_botched_orphan_cents_remnants(self) -> None:
        garbage = (
            "Total proposed investment: $154,026.32 "
            "($150,526.32 in professional fees plus $3,500 in direct travel expenses) "
            "32 ($150,526.32 in professional fees plus $3,500 in direct travel expenses) "
            "32 ($150,526.32 in professional fees plus $3,500 in direct travel expenses)."
        )
        cleaned = _rewrite_investment_sentence(
            garbage, 150_526.32, 3_500.0, 154_026.32
        )
        self.assertEqual(
            cleaned,
            "Total proposed investment: $154,026.32 "
            "($150,526.32 in professional fees plus $3,500 in direct travel expenses).",
        )


if __name__ == "__main__":
    unittest.main()
