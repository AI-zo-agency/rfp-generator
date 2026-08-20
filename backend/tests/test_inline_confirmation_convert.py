"""Inline/table confirm-before-submit prose → MANUAL FILL tags."""

from __future__ import annotations

import unittest

from app.services.proposal_manuscript import convert_inline_confirmation_phrases


class InlineConfirmationConvertTests(unittest.TestCase):
    def test_table_cell_confirm_becomes_manual_fill(self) -> None:
        raw = (
            "| ADDENDUM NUMBER | DATE ISSUED |\n"
            "| --- | --- |\n"
            "| Confirm before submit — Sonja confirm addendum number from Bonfire | "
            "Confirm before submit — Sonja confirm addendum date from Bonfire |"
        )
        out = convert_inline_confirmation_phrases(raw)
        self.assertIn("[MANUAL FILL: Sonja — Sonja confirm addendum number from Bonfire]", out)
        self.assertIn("[MANUAL FILL: Sonja — Sonja confirm addendum date from Bonfire]", out)


if __name__ == "__main__":
    unittest.main()
