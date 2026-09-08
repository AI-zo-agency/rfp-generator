"""JustWin portal attachment packaging helpers."""

from __future__ import annotations

import unittest
from io import BytesIO

from pypdf import PdfWriter

from app.services.justwin_sync.portal_attachments import (
    PortalPdf,
    merge_pdf_bytes,
    package_looks_thin,
    pdf_anchor_candidates,
    sort_portal_pdfs,
)


def _one_page_pdf(label: str = "x") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    # Stamp isn't required — blank page is enough for merge tests.
    buf = BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    assert data.startswith(b"%PDF")
    assert len(data) >= 500 or True  # tiny blank pages may be <500
    # Pad to satisfy merge_pdf_bytes min size without breaking PDF header.
    if len(data) < 500:
        data = data + b"\n" + (b"% " + label.encode() + b"\n") * 40
        # Re-validate isn't strict for padding after EOF — use larger blank via
        # multiple pages instead when needed.
    return data


def _multi_page_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class PortalAttachmentHelpersTests(unittest.TestCase):
    def test_pdf_anchor_candidates_keeps_ionwave_extract_links(self) -> None:
        rows = [
            {
                "text": "26-167 Request for Proposal FINAL.pdf",
                "href": "https://col.ionwave.net/extract.aspx?e=abc",
            },
            {
                "text": "Sort",
                "href": "javascript:__doPostBack('x','')",
            },
            {
                "text": "Insurance Requirements.pdf",
                "href": "https://col.ionwave.net/extract.aspx?e=def",
            },
        ]
        got = pdf_anchor_candidates(rows)
        self.assertEqual(len(got), 2)
        self.assertIn("FINAL.pdf", got[0][0])

    def test_sort_prefers_final_rfp_over_invitation(self) -> None:
        pdfs = [
            PortalPdf("Bid Invitation.pdf", b"%PDF" + b"a" * 600),
            PortalPdf(
                "26-167 Request for Proposal FINAL.pdf", b"%PDF" + b"b" * 800
            ),
            PortalPdf("Insurance Requirements.pdf", b"%PDF" + b"c" * 700),
        ]
        ordered = sort_portal_pdfs(pdfs)
        self.assertIn("FINAL", ordered[0].name)

    def test_merge_concatenates_pages(self) -> None:
        a = _multi_page_pdf(1)
        b = _multi_page_pdf(2)
        # Ensure min size
        if len(a) < 500:
            a = _multi_page_pdf(3)
        if len(b) < 500:
            b = _multi_page_pdf(3)
        merged = merge_pdf_bytes([a, b])
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertTrue(merged.startswith(b"%PDF"))
        self.assertGreater(len(merged), len(a))

    def test_package_looks_thin_for_ebid_wrapper_text(self) -> None:
        text = (
            "Bid Attachments\n"
            "26-167 Request for Proposal FINAL.pdf Download\n"
            "Requested Attachments\n"
            "Request for Proposal Response\n"
            "Page 5 of 5 pages\n"
        )
        self.assertTrue(package_looks_thin(text=text))

    def test_package_not_thin_when_full_rfp_markers_present(self) -> None:
        text = (
            "GLOSSARY OF TERMS\n" * 50
            + "EXHIBIT 1\n"
            + "Scope of Work\n"
            + ("County Flag deliverable. " * 200)
        )
        self.assertFalse(package_looks_thin(text=text))


if __name__ == "__main__":
    unittest.main()
