"""Surgical span-patch utility — Complete & Clean must patch, not rewrite."""

from __future__ import annotations

import unittest

from app.services.proposal_section_patch import (
    apply_targeted_edits,
    enforce_localized_edit,
    parse_targeted_edits,
)


class ParseTargetedEditsTests(unittest.TestCase):
    def test_parses_find_replace(self) -> None:
        self.assertEqual(
            parse_targeted_edits({"edits": [{"find": "a", "replace": "b"}]}),
            [("a", "b")],
        )

    def test_tolerates_key_aliases(self) -> None:
        self.assertEqual(
            parse_targeted_edits({"patches": [{"from": "x", "to": "y"}]}),
            [("x", "y")],
        )

    def test_skips_blank_find_and_bad_rows(self) -> None:
        self.assertEqual(
            parse_targeted_edits({"edits": [{"find": "  ", "replace": "z"}, "nope", {}]}),
            [],
        )


class ApplyTargetedEditsTests(unittest.TestCase):
    BODY = (
        "## Team\nElla Lindau is the Marketing Lead. "
        "We served 40 clients. Budget is $100,000 total."
    )

    def test_patches_only_the_named_span(self) -> None:
        out, n, changed, _ = apply_targeted_edits(
            self.BODY,
            [("Ella Lindau is the Marketing Lead.", "Ella Lindau is the Operations Director.")],
        )
        self.assertTrue(changed)
        self.assertEqual(n, 1)
        self.assertIn("Operations Director", out)
        # Everything else is byte-for-byte intact.
        self.assertIn("We served 40 clients.", out)
        self.assertIn("$100,000 total.", out)

    def test_span_not_present_is_skipped(self) -> None:
        out, n, changed, reason = apply_targeted_edits(self.BODY, [("not in body", "x")])
        self.assertFalse(changed)
        self.assertEqual(out, self.BODY)

    def test_short_section_fix_that_shrinks_is_allowed(self) -> None:
        # A one-sentence tab whose fabricated clause is trimmed — a large fraction
        # of a small section, but a legitimate targeted fix.
        body = "We hold 20 certifications and 50 awards this year."
        out, n, changed, _ = apply_targeted_edits(
            body, [("We hold 20 certifications and 50 awards this year.", "We hold 20 certifications.")]
        )
        self.assertTrue(changed)
        self.assertEqual(out, "We hold 20 certifications.")

    def test_mass_delete_of_substantial_section_refused(self) -> None:
        big = "\n".join(f"Line {i}: a substantive sentence about the work we deliver." for i in range(40))
        out, n, changed, reason = apply_targeted_edits(big, [(big, "tiny")])
        self.assertFalse(changed)
        self.assertEqual(out, big)
        self.assertIn("removed too much", reason)

    def test_ballooned_replacement_refused(self) -> None:
        out, n, changed, _ = apply_targeted_edits(
            self.BODY, [("40 clients", "40 clients " + "x" * 500)]
        )
        self.assertFalse(changed)

    def test_empty_result_refused(self) -> None:
        out, n, changed, reason = apply_targeted_edits("short body text here", [("short body text here", "")])
        self.assertFalse(changed)
        self.assertIn("emptied", reason)


class EnforceLocalizedEditTests(unittest.TestCase):
    BIG = "\n".join(
        f"Line {i}: we deliver tourism marketing services with proof and warmth."
        for i in range(30)
    )

    def test_refuses_wholesale_rewrite_of_good_section(self) -> None:
        body, accepted, reason = enforce_localized_edit(
            self.BIG, "## Brand Marketing Plan\n[MANUAL FILL: draft this]"
        )
        self.assertFalse(accepted)
        self.assertEqual(body, self.BIG)
        self.assertIn("wholesale", reason)

    def test_accepts_localized_edit(self) -> None:
        edited = self.BIG.replace("Line 3:", "Line 3 (updated):").replace("Line 7:", "Line 7 (updated):")
        body, accepted, _ = enforce_localized_edit(self.BIG, edited)
        self.assertTrue(accepted)
        self.assertEqual(body, edited)

    def test_short_section_change_is_allowed(self) -> None:
        body, accepted, _ = enforce_localized_edit("short stub tab", "a grounded fuller version of the tab")
        self.assertTrue(accepted)

    def test_append_only_completion_is_preserved(self) -> None:
        # Truncation-repair shape: completion keeps the whole original and adds to it.
        completed = self.BIG + "\nLine 30: a closing sentence that finishes the section."
        body, accepted, _ = enforce_localized_edit(self.BIG, completed)
        self.assertTrue(accepted)


if __name__ == "__main__":
    unittest.main()
