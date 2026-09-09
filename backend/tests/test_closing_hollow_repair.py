"""Hollow references + addenda MANUAL FILL spam repairs."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_closing_hollow_repair import (
    ensure_table_when_designer_note_promises_one,
    normalize_addenda_handoff_tables,
    normalize_hollow_addenda_content,
    references_section_is_hollow,
    repair_hollow_closing_sections,
    repair_hollow_references_section,
)
from app.services.proposal_manual_flags import (
    extract_manual_fill_tags,
    sanitize_nested_brackets_in_handoff_tags,
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

    def test_hollow_addenda_stub_with_nested_date_becomes_clean_table(self) -> None:
        raw = (
            "[MANUAL FILL: Sonja — confirm receipt of Addendum No. 1 dated [date] "
            "and update acknowledgment]"
        )
        out, changed = normalize_hollow_addenda_content(
            raw, title="Acknowledgment of Addenda"
        )
        self.assertTrue(changed)
        self.assertIn("None issued / none received", out)
        self.assertIn("| Addendum Number |", out)

    def test_nested_date_inside_manual_fill_sanitized(self) -> None:
        raw = (
            "[MANUAL FILL: Sonja — confirm Addendum No. 1 dated [date] on portal]"
        )
        cleaned = sanitize_nested_brackets_in_handoff_tags(raw)
        tags = extract_manual_fill_tags(cleaned)
        self.assertEqual(len(tags), 1)
        self.assertIn("(date)", tags[0].text)
        self.assertTrue(tags[0].text.endswith("]"))
        self.assertNotIn("[date]", cleaned)

    def test_designer_note_without_table_gets_markdown_shell(self) -> None:
        raw = (
            "zö will execute the engagement in four phases.\n\n"
            "[DESIGNER NOTE: four-column phase table — Discovery, Strategy, "
            "Production, Launch with owners and dates]"
        )
        out, changed = ensure_table_when_designer_note_promises_one(raw)
        self.assertTrue(changed)
        self.assertIn("| Item | Detail |", out)
        self.assertIn("[DESIGNER NOTE:", out)
        # Note still present — table comes first.
        self.assertLess(out.index("| Item |"), out.index("[DESIGNER NOTE:"))

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
        self.assertGreaterEqual(len(logs), 2)
        blob = "\n".join(s.content or "" for s in updated.sections)
        self.assertIn("None issued", blob)
        self.assertIn("provide three municipal", blob.casefold())

    def test_draft_scrubs_toc_title_and_method_designer_note(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="check",
                    title="10 Proposal Completion Checklist……………………….. 49",
                    content="Lead sentence.\n\n- Item one\n- Item two\n",
                ),
                ProposalSection(
                    id="method",
                    title="Method and Approach",
                    content=(
                        "We phase delivery for evaluators.\n\n"
                        "[DESIGNER NOTE: render as four-column phase table]"
                    ),
                ),
            ],
        )
        updated, logs = repair_hollow_closing_sections(draft)
        titles = {s.id: s.title for s in updated.sections}
        self.assertEqual(titles["check"], "Proposal Completion Checklist")
        method = next(s for s in updated.sections if s.id == "method")
        self.assertIn("| Item | Detail |", method.content or "")
        self.assertTrue(any("TOC leader" in x or "designer note" in x.casefold() for x in logs))


if __name__ == "__main__":
    unittest.main()
