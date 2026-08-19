"""Closing sections come from the ledger — not topic-mention regex.

Regex catalog retired. Obligation gate lives in the LLM extract prompt;
these tests lock the sync adapter + merge behavior with fixture ledgers.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from app.services.proposal_closing_ledger import ledger_from_fixture
from app.services.proposal_closing_package import detect_closing_components
from app.services.proposal_outline_dedup import merge_closing_components_into_outline


def _ids(ledger_items: list[dict], **kw) -> set[str]:
    ledger = ledger_from_fixture(ledger_items)
    return {c.id for c in detect_closing_components("ignored", ledger=ledger, **kw)}


class LedgerAuthorityTests(unittest.TestCase):
    def test_procedural_text_alone_does_not_add_sections(self) -> None:
        # Without a ledger, catalog scan is retired → empty.
        rfp = (
            "1.6 ADDENDA. The County may issue any addenda to this solicitation "
            "prior to the proposal due date. Vendors are responsible for "
            "monitoring ColoradoVSS and BidNet for addenda affecting this "
            "proposal. Addenda will be posted electronically."
        )
        self.assertEqual(detect_closing_components(rfp), [])

    def test_ledger_row_still_adds_addenda(self) -> None:
        self.assertIn(
            "addenda_acknowledgement",
            _ids(
                [
                    {
                        "id": "addenda_acknowledgement",
                        "title": "Acknowledgement of Addenda",
                        "kind": "form",
                    }
                ]
            ),
        )

    def test_commitment_section_is_not_added_unconditionally(self) -> None:
        rfp = "Section IV Documentation. Provide a company overview and pricing."
        self.assertNotIn(
            "offeror_commitment",
            {c.id for c in detect_closing_components(rfp)},
        )

    def test_empty_rfp_text_adds_nothing(self) -> None:
        self.assertEqual(detect_closing_components(""), [])

    def test_commitment_still_available_when_caller_opts_in(self) -> None:
        ids = {
            c.id
            for c in detect_closing_components(
                "Provide a company overview.",
                always_include_commitment=True,
            )
        }
        self.assertIn("offeror_commitment", ids)

    def test_genuine_reference_ledger_row_survives(self) -> None:
        self.assertIn(
            "references",
            _ids(
                [
                    {
                        "id": "references",
                        "title": "References",
                        "kind": "form",
                        "draftInstructions": "three client references",
                    }
                ]
            ),
        )


class InjectionSiteTests(unittest.TestCase):
    """ensure_closing_sections / merge must not force always_include_commitment=True."""

    ROOT = Path(__file__).resolve().parents[1] / "app" / "services"

    def _calls_with_always_true(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text())
        hits: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name != "detect_closing_components":
                continue
            for kw in node.keywords:
                if kw.arg == "always_include_commitment" and isinstance(
                    kw.value, ast.Constant
                ):
                    if kw.value.value is True:
                        hits.append(f"{path.name}:{node.lineno}")
        return hits

    def test_ensure_closing_sections_does_not_force_commitment(self) -> None:
        hits = self._calls_with_always_true(
            self.ROOT / "proposal_fulfill_rfp_gaps.py"
        )
        self.assertEqual(hits, [])

    def test_outline_merge_does_not_force_commitment(self) -> None:
        hits = self._calls_with_always_true(self.ROOT / "proposal_outline_dedup.py")
        self.assertEqual(hits, [])

    def test_merge_without_ledger_adds_nothing_even_if_rfp_mentions_forms(self) -> None:
        merged, added = merge_closing_components_into_outline(
            [],
            rfp_context=self.RFP if hasattr(self, "RFP") else (
                "All addenda must be acknowledged and returned with your proposal. "
                "Submit three references and the Pricing Proposal Form."
            ),
        )
        self.assertEqual(added, [])
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
