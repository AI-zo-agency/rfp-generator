"""Pricing uploads: what the KB upload box accepts, files and hides (no network)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.v1.knowledge_base import _check_pricing_upload
from app.services import pricing_kb, supermemory
from app.services.knowledge_base_document_types import category_title, is_valid_category

FIX = Path(__file__).parent / "fixtures" / "pricing_v2"
BOOK, RULES, INTERNAL = (
    (FIX / n).read_bytes() for n in ("01_Pricing_Book.md", "02_Rules_and_Wording.md", "03_Pricing_Internal.md")
)


class UploadCheck(unittest.TestCase):
    def test_template_docs_are_filed_by_their_header(self) -> None:
        self.assertEqual(
            _check_pricing_upload("pricing", "x.md", BOOK),
            ("pricing", "Pricing Book v2", {"pricingDoc": "Pricing Book", "pricingVersion": "v2"}),
        )
        cat, title, meta = _check_pricing_upload("pricing", "x.md", INTERNAL)
        self.assertEqual((cat, title, meta["pricingDoc"]), ("pricing_internal", "Pricing Internal v2", "Pricing Internal"))

    def test_broken_doc_is_refused_with_every_reason(self) -> None:
        bad = INTERNAL.decode().replace("| Margin floor | 53% |\n", "").replace("## Roles", "## People").encode()
        with self.assertRaises(HTTPException) as ctx:
            _check_pricing_upload("pricing", "x.md", bad)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Margin floor", ctx.exception.detail)
        self.assertIn("Roles", ctx.exception.detail)

    def test_pricing_type_takes_only_markdown_template(self) -> None:
        for name, body in (("sheet.pdf", b"%PDF"), ("old.md", b"# Old price sheet\n")):
            with self.assertRaises(HTTPException):
                _check_pricing_upload("pricing", name, body)

    def test_template_doc_under_another_type_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _check_pricing_upload("reference", "x.md", BOOK)
        self.assertIn("Choose the document type Pricing", ctx.exception.detail)

    def test_ordinary_docs_pass_through(self) -> None:
        self.assertIsNone(_check_pricing_upload("reference", "notes.md", b"# Notes\n"))
        self.assertIsNone(_check_pricing_upload("case_study", "cs.pdf", b"%PDF"))


class Hidden(unittest.TestCase):
    def test_internal_category_is_valid_but_not_a_user_choice(self) -> None:
        from app.services.knowledge_base_document_types import document_type_options

        self.assertTrue(is_valid_category("pricing_internal"))
        self.assertEqual(category_title("pricing_internal"), "Pricing (internal)")
        self.assertNotIn("pricing_internal", [o["value"] for o in document_type_options()])

    def test_internal_doc_never_appears_in_agent_search(self) -> None:
        self.assertIn(
            {"key": "category", "value": "pricing_internal", "negate": True},
            supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
        )
        self.assertFalse(supermemory.is_knowledge_base_hit({"metadata": {"category": "pricing_internal"}}))
        self.assertTrue(supermemory.is_knowledge_base_hit({"metadata": {"category": "pricing"}}))
        self.assertFalse(supermemory.is_knowledge_base_hit({"metadata": {"type": "rfp"}}))


class Status(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        pricing_kb._cache.clear()
        pricing_kb._failed.clear()

    def _docs(self, version: str, names: tuple[str, ...], cid: str) -> list[dict]:
        return [
            {"customId": f"kb:{cid}{n}", "updatedAt": "2026-10-01",
             "metadata": {"pricingDoc": n, "pricingVersion": version}}
            for n in names
        ]

    async def _say(self, docs: list[dict], version: str, texts: dict[str, str] | None = None) -> str:
        async def read(doc: dict) -> str:
            return (texts or {})[doc["customId"]]

        with patch("app.services.supermemory.list_all_container_documents", AsyncMock(return_value=docs)), \
             patch.object(pricing_kb, "_read_original", read):
            return await pricing_kb.describe_upload(version)

    async def test_first_upload_says_what_is_still_needed(self) -> None:
        msg = await self._say(self._docs("v2", ("Pricing Book",), "a"), "v2")
        self.assertIn("Still needed for v2: Rules and Wording, Pricing Internal", msg)
        self.assertIn("No pricing version is live yet", msg)

    async def test_complete_set_goes_live(self) -> None:
        names = pricing_kb.DOC_NAMES
        texts = {f"kb:a{n}": b.decode() for n, b in zip(names, (BOOK, RULES, INTERNAL))}
        msg = await self._say(self._docs("v2", names, "a"), "v2", texts)
        self.assertIn("Pricing v2 is live: 114 catalog items", msg)
        self.assertIn("December 31, 2026", msg)

    async def test_old_version_is_flagged_for_deletion_when_new_goes_live(self) -> None:
        names = pricing_kb.DOC_NAMES
        v2 = {f"kb:a{n}": b.decode() for n, b in zip(names, (BOOK, RULES, INTERNAL))}
        v3 = {k.replace("kb:a", "kb:b"): t.replace("| Version | v2 |", "| Version | v3 |") for k, t in v2.items()}
        msg = await self._say(self._docs("v2", names, "a") + self._docs("v3", names, "b"), "v3", {**v2, **v3})
        self.assertIn("Pricing v3 is live", msg)
        self.assertIn("Older docs (v2)", msg)


if __name__ == "__main__":
    unittest.main()
