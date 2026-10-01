"""Pricing docs: strict template parse, cross-doc checks, upload checks (no I/O)."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from app.services.pricing_kb import PricingDocError, build_book, check_doc

FIX = Path(__file__).parent / "fixtures" / "pricing_v2"
BOOK, RULES, INTERNAL = (
    (FIX / n).read_text() for n in ("01_Pricing_Book.md", "02_Rules_and_Wording.md", "03_Pricing_Internal.md")
)


def problems_of(**docs: str) -> list[str]:
    try:
        build_book(docs.get("book", BOOK), docs.get("rules", RULES), docs.get("internal", INTERNAL))
    except PricingDocError as exc:
        return exc.problems
    return []


class LoadV2(unittest.TestCase):
    def setUp(self) -> None:
        self.b = build_book(BOOK, RULES, INTERNAL)

    def test_header_and_settings(self) -> None:
        self.assertEqual(self.b.version, "v2")
        self.assertEqual(self.b.valid_through, date(2026, 12, 31))
        s = self.b.settings
        self.assertAlmostEqual(s.margin_floor, 0.53)
        self.assertAlmostEqual(s.cost_ratio, 0.47)
        self.assertEqual((s.target_multiple, s.blended_rate, s.min_price_per_hour), (3.7, 275, 220))
        self.assertAlmostEqual(s.nonprofit_discount, 0.12)
        self.assertAlmostEqual(s.traditional_commission, 0.15)
        self.assertEqual(self.b.negotiated_rates, {"city of bend": 250})

    def test_catalog_all_in_costs_match_the_clients_numbers(self) -> None:
        self.assertEqual(len(self.b.catalog), 114)
        # all-in cost from the client's methodology: hours x loaded + POs + hard cost
        for code, price, all_in in [("1a", 2100, 745), ("3f", 200, 215), ("6m", 31925, 12890), ("9t", 1399, 1424)]:
            item = self.b.catalog[code]
            self.assertEqual((item.price, self.b.all_in_cost(item)), (price, all_in), code)

    def test_media_fees_billing_and_wording(self) -> None:
        self.assertEqual([t.up_to for t in self.b.media_fees][:2], [10000, 15000])
        self.assertEqual(self.b.media_fees[-1].up_to, float("inf"))
        self.assertIn("Government Monthly", self.b.billing_terms)
        self.assertIn("Monthly Retainer", self.b.wording)
        self.assertIn("$275 an hour", self.b.wording["Rates"])

    def test_expiry(self) -> None:
        self.assertFalse(self.b.is_expired(date(2026, 12, 31)))
        self.assertTrue(self.b.is_expired(date(2027, 1, 1)))


class Refused(unittest.TestCase):
    def test_missing_setting_names_it(self) -> None:
        bad = INTERNAL.replace("| Margin floor | 53% |\n", "")
        self.assertTrue(any("Margin floor" in p for p in problems_of(internal=bad)))

    def test_missing_column_names_table_and_column(self) -> None:
        bad = BOOK.replace("| Code | Category | Item | Price |", "| Code | Category | Item | Cost |")
        self.assertTrue(any("Catalog" in p and "Price" in p for p in problems_of(book=bad)))

    def test_percent_setting_needs_percent_sign(self) -> None:
        bad = INTERNAL.replace("| Margin floor | 53% |", "| Margin floor | 53 |")
        self.assertTrue(any("Margin floor" in p and "%" in p for p in problems_of(internal=bad)))

    def test_catalog_code_without_cost_row(self) -> None:
        bad = "\n".join(l for l in INTERNAL.splitlines() if not l.startswith("| 4m |"))
        self.assertTrue(any("4m" in p and "Catalog costs" in p for p in problems_of(internal=bad)))

    def test_cost_row_without_price(self) -> None:
        bad = "\n".join(l for l in BOOK.splitlines() if not l.startswith("| 4m |"))
        self.assertTrue(any("4m" in p for p in problems_of(book=bad)))

    def test_version_mismatch(self) -> None:
        bad = RULES.replace("| Version | v2 |", "| Version | v3 |")
        self.assertTrue(any("version" in p.lower() for p in problems_of(rules=bad)))

    def test_non_numeric_cost(self) -> None:
        bad = INTERNAL.replace("| 1a | 5 | 1 |", "| 1a | five | 1 |")
        self.assertTrue(any("1a" in p and "five" in p for p in problems_of(internal=bad)))

    def test_missing_wording_block(self) -> None:
        bad = RULES.replace("### Change orders", "### Changes")
        self.assertTrue(any("Change orders" in p for p in problems_of(rules=bad)))

    def test_reports_every_problem_at_once(self) -> None:
        bad = INTERNAL.replace("| Margin floor | 53% |\n", "").replace("## Vendors", "## Suppliers")
        self.assertGreaterEqual(len(problems_of(internal=bad)), 2)


class UploadCheck(unittest.TestCase):
    def test_detects_each_doc(self) -> None:
        for md, name in ((BOOK, "Pricing Book"), (RULES, "Rules and Wording"), (INTERNAL, "Pricing Internal")):
            doc, version, probs = check_doc(md)
            self.assertEqual((doc, version, probs), (name, "v2", []))

    def test_not_a_pricing_doc(self) -> None:
        doc, version, probs = check_doc("# Old price sheet\n\nSome text")
        self.assertIsNone(doc)
        self.assertTrue(probs)

    def test_broken_doc_is_refused_with_reasons(self) -> None:
        doc, _, probs = check_doc(INTERNAL.replace("## Roles", "## People"))
        self.assertEqual(doc, "Pricing Internal")
        self.assertTrue(any("Roles" in p for p in probs))


class LoadFromKb(unittest.IsolatedAsyncioTestCase):
    """Set selection and fallback with a fake knowledge base."""

    def setUp(self) -> None:
        from app.services import pricing_kb

        pricing_kb._cache.clear()
        pricing_kb._failed.clear()
        self.pk = pricing_kb

    def _set(self, version: str, cid: str, internal: str = INTERNAL) -> tuple[list[dict], dict[str, str]]:
        def stamp(md: str) -> str:
            return md.replace("| Version | v2 |", f"| Version | {version} |")

        names = self.pk.DOC_NAMES
        docs = [
            {"customId": f"kb:{cid}{i}", "updatedAt": "2026-10-01",
             "metadata": {"pricingDoc": n, "pricingVersion": version}}
            for i, n in enumerate(names)
        ]
        texts = {f"kb:{cid}0": stamp(BOOK), f"kb:{cid}1": stamp(RULES), f"kb:{cid}2": stamp(internal)}
        return docs, texts

    async def _load(self, docs: list[dict], texts: dict[str, str]):
        from unittest.mock import AsyncMock, patch

        async def read(doc: dict) -> str:
            return texts[doc["customId"]]

        with patch("app.services.supermemory.list_all_container_documents", AsyncMock(return_value=docs)), \
             patch.object(self.pk, "_read_original", read):
            return await self.pk.load_pricing_book()

    async def test_newest_complete_set_wins(self) -> None:
        d2, t2 = self._set("v2", "a")
        d3, t3 = self._set("v3", "b")
        state = await self._load(d2 + d3, {**t2, **t3})
        self.assertEqual(state.book.version, "v3")

    async def test_incomplete_newer_set_is_ignored(self) -> None:
        d2, t2 = self._set("v2", "a")
        d3, t3 = self._set("v3", "b")
        state = await self._load(d2 + d3[:2], {**t2, **t3})
        self.assertEqual(state.book.version, "v2")

    async def test_invalid_newer_set_falls_back_and_says_why(self) -> None:
        d2, t2 = self._set("v2", "a")
        d3, t3 = self._set("v3", "b", internal=INTERNAL.replace("| Margin floor | 53% |\n", ""))
        state = await self._load(d2 + d3, {**t2, **t3})
        self.assertEqual(state.book.version, "v2")
        self.assertTrue(any("Margin floor" in p for p in state.skipped["v3"]))

    async def test_nothing_usable_raises_424(self) -> None:
        from app.services.proposal_common import ProposalError

        with self.assertRaises(ProposalError) as ctx:
            await self._load([], {})
        self.assertEqual(ctx.exception.status_code, 424)


if __name__ == "__main__":
    unittest.main()
