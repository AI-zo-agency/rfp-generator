"""rfp_window_around_section_title prefers the form page over the TOC listing."""

from __future__ import annotations

import unittest

from app.services.proposal_rfp_excerpt import rfp_window_around_section_title


class RfpWindowAroundSectionTitleTests(unittest.TestCase):
    def test_skips_toc_and_returns_form_body(self) -> None:
        toc = (
            "TABLE OF CONTENTS\n"
            "Attachment A, Statement of Qualifications ...... 19\n"
            "Attachment B .................................. 23\n"
        )
        filler = "Scope of work filler paragraph.\n" * 80
        form = (
            "ATTACHMENT A STATEMENT OF QUALIFICATIONS\n\n"
            "Legal Name of Respondent as it will appear on any final Agreement:\n"
            "_______________________________________________\n"
            "The Respondent is a (check one):\n"
            "( ) Sole Proprietorship\n"
            "( ) Corporation — STATE OF INCORPORATION: _____________\n"
        )
        body = toc + filler + form
        out = rfp_window_around_section_title(
            body,
            "Attachment A, Statement of Qualifications",
            max_chars=4_000,
        )
        self.assertIn("Legal Name of Respondent", out)
        self.assertIn("Sole Proprietorship", out)
        # Should not be TOC-only
        self.assertNotEqual(out.strip(), toc.strip())

    def test_checklist_line_does_not_beat_form_heading_without_comma(self) -> None:
        """Port RFQ shape: list uses 'Attachment A, SOQ'; form is ATTACHMENT A\\nSOQ."""
        checklist = (
            "G. Required Documents\n"
            "a. Attachment A, Statement of Qualifications\n"
            "b. Attachment B, Exceptions to Agreement\n"
            "SBE status, indicate on Attachment A, Question D\n"
        )
        filler = "Qualifications Package narrative.\n" * 100
        form = (
            "ATTACHMENT A\n"
            "STATEMENT OF QUALIFICATIONS\n\n"
            "Legal Name of Respondent as it will appear on any final Agreement:\n"
            "_______________________________________________\n"
            "( ) Sole Proprietorship\n"
            "I, _________________ (PRINT NAME), affirm that all the information\n"
        )
        body = checklist + filler + form
        out = rfp_window_around_section_title(
            body,
            "Attachment A, Statement of Qualifications",
            max_chars=3_000,
        )
        self.assertIn("Legal Name of Respondent", out)
        self.assertIn("PRINT NAME", out)
        self.assertNotIn("Required Documents", out[:200])


if __name__ == "__main__":
    unittest.main()
