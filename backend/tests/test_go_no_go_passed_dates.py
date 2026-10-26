"""Go/No-Go conditions must not tell the team to act by a date that has passed."""

from __future__ import annotations

import unittest
from datetime import date

from app.services.go_no_go_service import mark_passed_dates


class PassedDateTests(unittest.TestCase):
    TODAY = date(2026, 10, 26)

    def test_question_deadline_in_the_past_is_marked(self) -> None:
        """Minnesota Lottery: 'Submit written questions by the Oct 16 deadline' on Oct 26."""
        text = (
            "Submit written questions by the Oct 16 deadline if still open; otherwise "
            "review the Oct 27 Q&A answers [Proposal Lead]"
        )
        out = mark_passed_dates(text, self.TODAY)
        self.assertIn("Oct 16 has passed", out)
        self.assertNotIn("Oct 27 has passed", out)

    def test_numeric_and_full_month_dates(self) -> None:
        self.assertIn(
            "10/16/26 has passed",
            mark_passed_dates("Questions due 10/16/26.", self.TODAY),
        )
        self.assertIn(
            "October 1, 2026 has passed",
            mark_passed_dates("Register by October 1, 2026.", self.TODAY),
        )

    def test_future_and_historical_dates_are_left_alone(self) -> None:
        text = "Proposals due November 20, 2026; the prior contract began December 12, 2010."
        self.assertEqual(mark_passed_dates(text, self.TODAY), text)

    def test_marks_once(self) -> None:
        once = mark_passed_dates("Submit questions by Oct 16.", self.TODAY)
        self.assertEqual(mark_passed_dates(once, self.TODAY), once)
