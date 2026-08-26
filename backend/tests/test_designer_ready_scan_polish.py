"""Complete Scan designer-ready markup polish — no invented facts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_manuscript import (
    apply_designer_ready_markup_polish_to_draft,
    normalize_designer_note_markup,
)


class DesignerReadyScanPolishTests(unittest.TestCase):
    def test_normalize_bold_designer_note_to_bracket_tag(self) -> None:
        body = (
            "Lead sentence.\n\n"
            "**Designer Note:** Attach signed cover PDF — Sonja to sign.\n\n"
            "Closing line."
        )
        out = normalize_designer_note_markup(body)
        self.assertIn("[DESIGNER NOTE: Attach signed cover PDF — Sonja to sign.]", out)
        self.assertNotIn("**Designer Note:**", out)

    def test_polish_draft_collapses_empty_heading_and_normalizes_note(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-polish",
            sections=[
                ProposalSection(
                    id="s1",
                    title="1.1 — Who We Are",
                    content=(
                        "We are a branding agency.\n\n"
                        "## Empty Shell\n\n"
                        "## Real Subsection\n\n"
                        "Substance here.\n\n"
                        "Designer Note: Place bio PDFs in Section 2.\n"
                    ),
                    wordTarget=200,
                    status="generated",
                )
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )
        out, logs = apply_designer_ready_markup_polish_to_draft(draft)
        body = out.sections[0].content or ""
        self.assertIn("[DESIGNER NOTE: Place bio PDFs in Section 2.]", body)
        self.assertNotIn("## Empty Shell", body)
        self.assertIn("## Real Subsection", body)
        self.assertTrue(logs)


if __name__ == "__main__":
    unittest.main()
