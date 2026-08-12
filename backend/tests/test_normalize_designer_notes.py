"""Canonical [DESIGNER NOTE:] markup — not bold prose labels."""

from __future__ import annotations

import unittest

from app.services.proposal_manuscript import (
    normalize_designer_note_markup,
    scrub_client_facing_section_artifacts,
)


class NormalizeDesignerNotesTests(unittest.TestCase):
    def test_bold_designer_note_becomes_bracket_tag(self) -> None:
        raw = (
            "You maintain full monthly operations capacity.\n\n"
            "**Designer Note:** This section establishes the clear boundary "
            "between included monthly services and optional services."
        )
        out = normalize_designer_note_markup(raw)
        self.assertIn("[DESIGNER NOTE:", out)
        self.assertNotIn("**Designer Note:**", out)
        self.assertIn("clear boundary", out)

    def test_plain_designer_note_label(self) -> None:
        raw = "Body prose.\n\nDesigner Note: Place a callout box after this paragraph."
        out = normalize_designer_note_markup(raw)
        self.assertEqual(
            out.strip(),
            "Body prose.\n\n[DESIGNER NOTE: Place a callout box after this paragraph.]",
        )

    def test_already_canonical_unchanged(self) -> None:
        raw = "Body.\n\n[DESIGNER NOTE: Render as a full-width callout box.]"
        self.assertEqual(normalize_designer_note_markup(raw), raw)

    def test_scrub_runs_normalizer(self) -> None:
        raw = "Prose.\n\n**Designer Note:** Attach signed task-auth form as inset."
        out = scrub_client_facing_section_artifacts(raw)
        self.assertIn("[DESIGNER NOTE: Attach signed task-auth form as inset.]", out)
        self.assertNotIn("**Designer Note:**", out)


if __name__ == "__main__":
    unittest.main()
