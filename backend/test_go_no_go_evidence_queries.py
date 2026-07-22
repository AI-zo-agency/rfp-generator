"""Unit tests for Go/No-Go evidence-query hygiene (no live Supermemory)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.go_no_go_service import (
    RfpContentInfo,
    _annotate_go_no_go_hit,
    _deterministic_evidence_queries,
)


def _rfp(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": "rfp-test",
        "title": "Destination Brand Marketing",
        "client": "Hawaiʻi Tourism Authority",
        "sector": "Tourism",
        "location": "Hawaiʻi",
        "due_date": None,
        "estimated_value": None,
        "pdf_path": None,
        "description": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _content(text: str) -> RfpContentInfo:
    return RfpContentInfo(
        pdf_path=None,
        pdf_path_recorded=None,
        pdf_file_missing=False,
        pdf_exists=False,
        pdf_page_count=0,
        pdf_image_only=False,
        pdf_text=text,
        description="",
        substantive_chars=len(text),
        metadata_only=False,
    )


class GoNoGoEvidenceQueryTests(unittest.TestCase):
    def test_deterministic_queries_include_fin_won_and_certs(self) -> None:
        rfp = _rfp()
        content = _content(
            "The Offeror must have or must establish an office in Oceania. "
            "MCI meetings are excluded. Destination brand marketing for leisure visitors."
        )
        queries = _deterministic_evidence_queries(rfp, content)  # type: ignore[arg-type]
        blob = " | ".join(queries).casefold()
        self.assertIn("07_fin", blob)
        self.assertIn("06_won", blob)
        self.assertIn("vishal", blob)
        self.assertIn("oceania", blob)
        self.assertIn("san francisco travel", blob)

    def test_annotate_fin_hit(self) -> None:
        hit = {
            "title": "07_FIN_CityofSanLeandro_Proposal.pdf",
            "content": "Resonance prepared Lynchburg Economic Development materials.",
        }
        annotated = _annotate_go_no_go_hit(hit)
        title = str(annotated.get("title") or "")
        self.assertIn("FINALIST/LOSS", title)
        self.assertIn("Resonance", title)


if __name__ == "__main__":
    unittest.main()
