"""Ask Ralph rewrite of a form tab reads the form's own RFP pages, not the RFP head."""

from __future__ import annotations

import unittest

from app.services.proposal_section_editor import _redraft_rfp_excerpt

_TITLE = "Attachment B: Proposer Certifications and Representations"
_RFP_CONTEXT = "Request for Proposals — City letterhead and table of contents. " * 200
_FULL_TEXT = (
    _RFP_CONTEXT
    + ("Scope of work filler. " * 7_000)
    + "\nB-1\nATTACHMENT B\nPROPOSER CERTIFICATIONS AND REPRESENTATIONS\n"
    "1. ACKNOWLEDGEMENT OF RECEIPT OF ADDENDA\n"
    "Employer ID Number State of Incorporation\n"
)


class RedraftRfpExcerptTests(unittest.TestCase):
    def test_form_tab_gets_the_form_pages(self) -> None:
        excerpt = _redraft_rfp_excerpt(
            rfp_context=_RFP_CONTEXT,
            rfp_full_text=_FULL_TEXT,
            section_title=_TITLE,
            instrument="disclosure",
        )
        self.assertIn("ACKNOWLEDGEMENT OF RECEIPT OF ADDENDA", excerpt)
        self.assertIn("Employer ID Number", excerpt)

    def test_narrative_tab_keeps_the_rfp_head(self) -> None:
        excerpt = _redraft_rfp_excerpt(
            rfp_context=_RFP_CONTEXT,
            rfp_full_text=_FULL_TEXT,
            section_title=_TITLE,
            instrument="narrative",
        )
        self.assertEqual(excerpt, _RFP_CONTEXT[:4000])

    def test_form_missing_from_rfp_falls_back_to_head(self) -> None:
        excerpt = _redraft_rfp_excerpt(
            rfp_context=_RFP_CONTEXT,
            rfp_full_text=_FULL_TEXT,
            section_title="Attachment Z: Vendor Questionnaire",
            instrument="form",
        )
        self.assertEqual(excerpt, _RFP_CONTEXT[:4000])


if __name__ == "__main__":
    unittest.main()
