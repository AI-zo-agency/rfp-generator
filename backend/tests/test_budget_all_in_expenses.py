"""MANUAL FILL tags survive a rewrite: masking round-trip and dropped-tag detection."""

from __future__ import annotations

import unittest

from app.services.proposal_manual_flags import (
    mask_manual_fill_tags,
    missing_manual_fill_placeholders,
    unmask_manual_fill_tags,
)


class ManualFillMaskTests(unittest.TestCase):
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
