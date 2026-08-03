"""The rate card must be stable across runs and correctable by a human once.

Before: build_pricing_rate_card_from_guide_text re-parsed 00_Guide_Pricing on
every run, so the same guide could yield a different card each time, and any
rate the parser missed produced an unbound line item that had to be fixed by
hand on every proposal.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.models.pricing_rate_card import PricingRate, PricingRateCard
from app.services import pricing_rate_card_store as store

GUIDE = "Web development Average $165/hour. Brand strategy fixed $12,000."


def _card(*rates: PricingRate) -> PricingRateCard:
    return PricingRateCard(rates=list(rates), guideExcerptChars=len(GUIDE))


def _rate(rate_id: str, amount: float, service: str = "Web development") -> PricingRate:
    return PricingRate(
        rateId=rate_id, service=service, tier="Average", unit="hour", amount=amount
    )


class FingerprintTests(unittest.TestCase):
    def test_same_guide_same_fingerprint(self) -> None:
        self.assertEqual(store.guide_fingerprint(GUIDE), store.guide_fingerprint(GUIDE))

    def test_edited_guide_changes_fingerprint(self) -> None:
        self.assertNotEqual(
            store.guide_fingerprint(GUIDE), store.guide_fingerprint(GUIDE + " New line.")
        )


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._patch = mock.patch.object(
            store, "_CACHE_PATH", Path(self._tmp.name) / "cache.json"
        )
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_guide_is_parsed_once_then_reused(self) -> None:
        parsed = _card(_rate("web-dev", 165))
        builder = mock.Mock(return_value=parsed)

        with mock.patch(
            "app.services.pricing_rate_card_builder."
            "build_pricing_rate_card_from_guide_text",
            builder,
        ), mock.patch.object(store, "load_overrides", return_value=[]):
            first = store.build_stable_rate_card(GUIDE)
            second = store.build_stable_rate_card(GUIDE)

        self.assertEqual(builder.call_count, 1, "second run must not re-parse")
        self.assertEqual(
            [r.amount for r in first.rates], [r.amount for r in second.rates]
        )

    def test_edited_guide_triggers_a_fresh_parse(self) -> None:
        builder = mock.Mock(return_value=_card(_rate("web-dev", 165)))
        with mock.patch(
            "app.services.pricing_rate_card_builder."
            "build_pricing_rate_card_from_guide_text",
            builder,
        ), mock.patch.object(store, "load_overrides", return_value=[]):
            store.build_stable_rate_card(GUIDE)
            store.build_stable_rate_card(GUIDE + " Updated 2026.")

        self.assertEqual(builder.call_count, 2)

    def test_unreadable_cache_never_breaks_a_run(self) -> None:
        store._CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        store._CACHE_PATH.write_text("{ not json", encoding="utf-8")
        builder = mock.Mock(return_value=_card(_rate("web-dev", 165)))

        with mock.patch(
            "app.services.pricing_rate_card_builder."
            "build_pricing_rate_card_from_guide_text",
            builder,
        ), mock.patch.object(store, "load_overrides", return_value=[]):
            card = store.build_stable_rate_card(GUIDE)

        self.assertEqual(len(card.rates), 1)


class OverrideTests(unittest.TestCase):
    def test_override_replaces_a_wrong_parsed_rate(self) -> None:
        card = _card(_rate("web-dev", 100))
        merged = store.apply_overrides(card, [_rate("web-dev", 165)])

        amounts = {r.rate_id: r.amount for r in merged.rates}
        self.assertEqual(amounts["web-dev"], 165)
        self.assertEqual(len(merged.rates), 1)

    def test_override_supplies_a_rate_the_parser_missed(self) -> None:
        card = _card(_rate("web-dev", 165))
        merged = store.apply_overrides(card, [_rate("seo", 140, service="SEO")])

        self.assertEqual(len(merged.rates), 2)
        self.assertIn("seo", {r.rate_id for r in merged.rates})

    def test_override_survives_the_cache(self) -> None:
        """A correction must apply even when the card comes from cache."""
        with TemporaryDirectory() as tmp:
            with mock.patch.object(store, "_CACHE_PATH", Path(tmp) / "c.json"):
                builder = mock.Mock(return_value=_card(_rate("web-dev", 100)))
                with mock.patch(
                    "app.services.pricing_rate_card_builder."
                    "build_pricing_rate_card_from_guide_text",
                    builder,
                ):
                    with mock.patch.object(store, "load_overrides", return_value=[]):
                        store.build_stable_rate_card(GUIDE)  # populates cache
                    with mock.patch.object(
                        store, "load_overrides", return_value=[_rate("web-dev", 165)]
                    ):
                        corrected = store.build_stable_rate_card(GUIDE)

                self.assertEqual(builder.call_count, 1)
                self.assertEqual(corrected.rates[0].amount, 165)

    def test_override_applies_on_the_first_parse_too(self) -> None:
        """Not just via cache: a correction must land on a cold run as well."""
        with TemporaryDirectory() as tmp:
            with mock.patch.object(store, "_CACHE_PATH", Path(tmp) / "c.json"):
                builder = mock.Mock(return_value=_card(_rate("web-dev", 100)))
                with mock.patch(
                    "app.services.pricing_rate_card_builder."
                    "build_pricing_rate_card_from_guide_text",
                    builder,
                ), mock.patch.object(
                    store, "load_overrides", return_value=[_rate("web-dev", 165)]
                ):
                    card = store.build_stable_rate_card(GUIDE)

        self.assertEqual(builder.call_count, 1)
        self.assertEqual(card.rates[0].amount, 165)

    def test_no_overrides_leaves_the_card_untouched(self) -> None:
        card = _card(_rate("web-dev", 165))
        self.assertIs(store.apply_overrides(card, []), card)

    def test_malformed_override_entry_is_skipped_not_fatal(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            path.write_text(
                json.dumps({"rates": [{"nope": 1}, {
                    "rateId": "seo", "service": "SEO", "unit": "hour", "amount": 140
                }]}),
                encoding="utf-8",
            )
            with mock.patch.object(store, "_OVERRIDES_PATH", path):
                loaded = store.load_overrides()

        self.assertEqual([r.rate_id for r in loaded], ["seo"])

    def test_missing_override_file_is_fine(self) -> None:
        with mock.patch.object(store, "_OVERRIDES_PATH", Path("/nonexistent/x.json")):
            self.assertEqual(store.load_overrides(), [])


class ShippedOverrideFileTests(unittest.TestCase):
    def test_the_committed_file_parses(self) -> None:
        self.assertTrue(store._OVERRIDES_PATH.exists(), store._OVERRIDES_PATH)
        data = json.loads(store._OVERRIDES_PATH.read_text(encoding="utf-8"))
        self.assertIn("rates", data)
        self.assertIsInstance(data["rates"], list)


if __name__ == "__main__":
    unittest.main()
