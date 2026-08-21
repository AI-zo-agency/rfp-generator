"""Hollow references + addenda MANUAL FILL spam repairs."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_closing_hollow_repair import (
    normalize_addenda_handoff_tables,
    references_section_is_hollow,
    repair_hollow_closing_sections,
    repair_hollow_references_section,
)


class ClosingHollowRepairTests(unittest.TestCase):
    def test_addenda_manual_fill_spam_becomes_clean_table(self) -> None:
        raw = (
            "## Acknowledgment of Addenda\n\n"
            "| Addendum Number | Issue Date | Description |\n"
            "| --- | --- | --- |\n"
            "| [MANUAL FILL: Sonja — confirm addendum number] | "
            "[MANUAL FILL: Sonja — confirm addendum date] | "
            "[MANUAL FILL: Sonja — confirm description] |\n"
            "| [MANUAL FILL: Sonja — confirm addendum number] | "
            "[MANUAL FILL: Sonja — confirm addendum date] | "
            "[MANUAL FILL: Sonja — confirm description] |\n"
        )
        out, changed = normalize_addenda_handoff_tables(raw)
        self.assertTrue(changed)
        self.assertIn("None issued / none received", out)
        self.assertEqual(out.count("[MANUAL FILL"), 1)
        self.assertIn("confirm on the buyer portal", out.casefold())

    def test_hollow_references_gets_handoff(self) -> None:
        raw = (
            "We provide three municipal references below. Each engagement involved "
            "comprehensive brand development with multi-stakeholder coordination."
        )
        self.assertTrue(references_section_is_hollow(raw))
        out, changed = repair_hollow_references_section(
            raw, title="Section 5.6 — References"
        )
        self.assertTrue(changed)
        self.assertIn("[MANUAL FILL: Sonja — provide three", out)
        self.assertNotIn("references below", out.casefold())

    def test_draft_repair_hits_both(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="ref",
                    title="Section 5.6 — References (Minimum Three)",
                    content="We provide three municipal references below. Comparable scope.",
                ),
                ProposalSection(
                    id="add",
                    title="Acknowledgment of Addenda",
                    content=(
                        "| Addendum Number | Issue Date |\n"
                        "| --- | --- |\n"
                        "| [MANUAL FILL: a] | [MANUAL FILL: b] |\n"
                    ),
                ),
            ],
        )
        updated, logs = repair_hollow_closing_sections(draft)
        self.assertEqual(len(logs), 2)
        blob = "\n".join(s.content or "" for s in updated.sections)
        self.assertIn("None issued", blob)
        self.assertIn("provide three municipal", blob.casefold())


if __name__ == "__main__":
    unittest.main()
