"""Due-date extraction prefers proposal deadline over Q&A / pre-bid."""

from __future__ import annotations

import unittest

from app.services.rfp_due_date import extract_due_date_from_text


class RfpDueDateExtractTests(unittest.TestCase):
    def test_hawaii_cover_prefers_august_31_over_qa(self) -> None:
        text = """
        STATE OF HAWAIʻI
        DEPARTMENT OF LAND AND NATURAL RESOURCES
        REQUEST FOR PROPOSALS
        No. RFP SEA 26

        Pre-Bid Date: August 6, 2026
        Q&A Due Date: August 6, 2026

        SEALED PROPOSALS will be received up to 5:00 PM (HST) ON AUGUST 31, 2026
        at the Department of Land and Natural Resources.
        """
        self.assertEqual(extract_due_date_from_text(text), "2026-08-31")

    def test_explicit_proposal_due_date(self) -> None:
        text = "Proposal due date: September 15, 2026. Questions due August 1, 2026."
        self.assertEqual(extract_due_date_from_text(text), "2026-09-15")

    def test_qa_alone_still_extracts(self) -> None:
        text = "Q&A Due Date: August 6, 2026"
        self.assertEqual(extract_due_date_from_text(text), "2026-08-06")


if __name__ == "__main__":
    unittest.main()
