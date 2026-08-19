"""Word export must keep tables readable (no one-character column collapse)."""

from __future__ import annotations

import io
import unittest

from docx import Document
from docx.oxml.ns import qn

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_docx_export import (
    _shape_table_for_word,
    build_proposal_docx_bytes,
)


class WordTableLayoutTests(unittest.TestCase):
    def test_wide_one_row_table_becomes_item_detail(self) -> None:
        headers = [
            "Years in Operation",
            "Business Registrations",
            "Legal Structure",
            "Certifications",
            "Grant Experience",
            "Invoicing Readiness",
        ]
        rows = [
            [
                "13 years",
                "Oregon, Washington, Texas, Colorado, and California",
                "S-Corp/LLC, sole owner Sonja Anderson",
                "WBENC Women's Business Enterprise",
                "Public health / behavioral health campaigns",
                "Quarterly invoicing aligned to grant spend-down",
            ]
        ]
        hdr, data = _shape_table_for_word(headers, rows)
        self.assertEqual(hdr, ["Item", "Detail"])
        self.assertEqual(len(data), 6)
        self.assertEqual(data[1][0], "Business Registrations")
        self.assertIn("Oregon", data[1][1])

    def test_two_column_table_stays_two_column(self) -> None:
        hdr, data = _shape_table_for_word(
            ["Field", "Detail"],
            [["Years in Operation", "13"], ["Federal EIN", "47-4333943"]],
        )
        self.assertEqual(hdr, ["Field", "Detail"])
        self.assertEqual(len(data), 2)

    def test_exported_tables_have_fixed_page_width(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="financial",
                    title="Financial Stability",
                    content=(
                        "## Financial Stability\n\n"
                        "| Years in Operation | Business Registrations | Legal Structure | "
                        "Certifications | Grant Experience |\n"
                        "| --- | --- | --- | --- | --- |\n"
                        "| 13 years | Oregon, Washington, Texas, Colorado, and California | "
                        "S-Corp/LLC | WBENC | Public health campaigns |\n"
                    ),
                    status="generated",
                )
            ],
        )
        blob = build_proposal_docx_bytes(doc_title="Calvert County", draft=draft)
        doc = Document(io.BytesIO(blob))
        self.assertTrue(doc.tables)
        table = doc.tables[0]
        self.assertEqual(len(table.columns), 2)
        texts = [cell.text for row in table.rows for cell in row.cells]
        self.assertTrue(any("Oregon" in t for t in texts))
        self.assertTrue(any("Business Registrations" in t for t in texts))
        tbl_pr = table._tbl.tblPr
        tbl_w = tbl_pr.find(qn("w:tblW"))
        layout = tbl_pr.find(qn("w:tblLayout"))
        self.assertIsNotNone(tbl_w)
        self.assertEqual(tbl_w.get(qn("w:type")), "dxa")
        self.assertGreater(int(tbl_w.get(qn("w:w"))), 8000)
        self.assertEqual(layout.get(qn("w:type")), "fixed")


if __name__ == "__main__":
    unittest.main()
