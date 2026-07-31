"""T1 deterministic gates: internal note leaks + truncation artifacts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_t1_validators import (
    scan_all_t1,
    scan_internal_note_leaks,
    scan_truncation_artifacts,
    t1_findings_as_blocker_messages,
)


def _section(
    content: str,
    *,
    section_id: str = "s1",
    title: str = "Approach",
) -> ProposalSection:
    return ProposalSection(
        id=section_id,
        title=title,
        content=content,
        status="generated",
    )


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        updatedAt="2026-01-01T00:00:00Z",
        sections=list(sections),
    )


class InternalNoteLeakTests(unittest.TestCase):
    def test_flags_flag_for_tag(self) -> None:
        draft = _draft(
            _section(
                "Partners include [FLAG FOR SONJA: Add Recovery Network of Oregon].",
                section_id="s18",
                title="Partners",
            )
        )
        findings = scan_internal_note_leaks(draft)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["category"], "note_leak")
        self.assertEqual(f["severity"], "critical")
        self.assertTrue(f["blocker"])
        self.assertEqual(f["section_id"], "s18")
        self.assertIn("FLAG FOR", f["message"].upper())

    def test_flags_flag_for_case_insensitive(self) -> None:
        draft = _draft(_section("See [flag for team: confirm hours]."))
        findings = scan_internal_note_leaks(draft)
        self.assertTrue(any(f["category"] == "note_leak" for f in findings))

    def test_flags_todo_fix_xxx_whole_words(self) -> None:
        for token in ("TODO", "FIXME", "XXX"):
            with self.subTest(token=token):
                draft = _draft(_section(f"Status: {token} before submit."))
                findings = scan_internal_note_leaks(draft)
                self.assertTrue(
                    findings,
                    msg=f"expected note_leak for whole-word {token}",
                )
                self.assertTrue(all(f["blocker"] for f in findings))
                self.assertTrue(all(f["severity"] == "critical" for f in findings))

    def test_does_not_flag_todo_inside_words(self) -> None:
        draft = _draft(
            _section("The methodology includes a rigorous approach to outcomes.")
        )
        findings = scan_internal_note_leaks(draft)
        self.assertEqual(findings, [])

    def test_flags_internal_bracketed_production_notes(self) -> None:
        cases = [
            "[INTERNAL: remove before ship]",
            "[FOR SONJA: add logo]",
            "[INTERNAL NOTE: stub]",
        ]
        for text in cases:
            with self.subTest(text=text):
                draft = _draft(_section(f"Content {text} more."))
                findings = scan_internal_note_leaks(draft)
                self.assertTrue(findings, msg=f"expected leak for {text}")

    def test_does_not_flag_verify_tags(self) -> None:
        draft = _draft(
            _section("Staffing: [VERIFY: confirm FTE count with finance].")
        )
        findings = scan_internal_note_leaks(draft)
        self.assertEqual(findings, [])

    def test_does_not_flag_manual_fill_variants(self) -> None:
        variants = [
            "[MANUAL FILL]",
            "[MANUAL FILL: insert local address]",
            "[MANUAL FILL or N/A]",
        ]
        for tag in variants:
            with self.subTest(tag=tag):
                draft = _draft(_section(f"Address: {tag}."))
                findings = scan_internal_note_leaks(draft)
                self.assertEqual(findings, [], msg=f"should not flag {tag}")

    def test_does_not_flag_designer_note(self) -> None:
        draft = _draft(
            _section("Layout: [DESIGNER NOTE: place map on facing page].")
        )
        findings = scan_internal_note_leaks(draft)
        self.assertEqual(findings, [])

    def test_empty_section_no_findings(self) -> None:
        draft = _draft(_section(""))
        findings = scan_internal_note_leaks(draft)
        self.assertEqual(findings, [])


class TruncationArtifactTests(unittest.TestCase):
    def test_flags_trailing_currency_fragment(self) -> None:
        draft = _draft(
            _section(
                "Total Year 1 client invoicing: $325,242.66. 66 ($325,242.",
                section_id="s14",
                title="Budget Narrative",
            )
        )
        findings = scan_truncation_artifacts(draft)
        self.assertTrue(findings)
        self.assertTrue(any(f["category"] == "truncation" for f in findings))
        self.assertTrue(all(f["severity"] == "critical" for f in findings))
        self.assertTrue(all(f["blocker"] for f in findings))

    def test_flags_incomplete_currency_ending(self) -> None:
        draft = _draft(_section("Projected fee is $325,242."))
        findings = scan_truncation_artifacts(draft)
        # Trailing incomplete currency like $325,242. (no cents / cut mid-number)
        # The CVVB pattern ends with incomplete open paren + currency fragment.
        # Bare "$325,242." alone is a truncated decimal — flag it.
        self.assertTrue(
            any(f["category"] == "truncation" for f in findings),
            msg="incomplete currency fragment should be flagged",
        )

    def test_flags_repeated_token_currency_tail(self) -> None:
        draft = _draft(
            _section("Total Year 1 client invoicing: $325,242.66. 66 ($325,242.")
        )
        findings = scan_truncation_artifacts(draft)
        codes = {f["code"] for f in findings}
        self.assertTrue(
            codes,
            msg="expected at least one truncation code for repeated-token tail",
        )

    def test_flags_mid_sentence_cutoff(self) -> None:
        draft = _draft(
            _section(
                "Our team brings deep public-sector experience.\n"
                "Full resumes and bio summaries for each named",
                section_id="s30",
                title="Key Personnel",
            )
        )
        findings = scan_truncation_artifacts(draft)
        self.assertTrue(
            any("mid" in f["code"].lower() or "sentence" in f["message"].lower()
                or "cutoff" in f["message"].lower() or "truncat" in f["message"].lower()
                for f in findings),
            msg=f"expected mid-sentence cutoff finding, got {findings}",
        )
        self.assertTrue(all(f["blocker"] for f in findings))

    def test_does_not_flag_complete_sentence(self) -> None:
        draft = _draft(
            _section("Full resumes and bio summaries for each named staff member.")
        )
        findings = scan_truncation_artifacts(draft)
        self.assertEqual(findings, [])

    def test_does_not_flag_manual_fill_tail_after_complete_sentence(self) -> None:
        draft = _draft(
            _section(
                "We serve public universities with clarity.\n\n"
                "[MANUAL FILL: Sonja — Section ends mid-sentence without terminal punctuation]",
                section_id="s-mf",
                title="Who We Are",
            )
        )
        findings = scan_truncation_artifacts(draft)
        self.assertFalse(
            any(f["code"] == "t1.truncation.mid_sentence_cutoff" for f in findings),
            msg=f"handoff-tag tail must not count as truncation, got {findings}",
        )

    def test_does_not_flag_table_ending_without_period(self) -> None:
        draft = _draft(
            _section(
                "| Role | Hours |\n"
                "| --- | --- |\n"
                "| Director | 120 |\n"
                "| Analyst | 80 |"
            )
        )
        findings = scan_truncation_artifacts(draft)
        mid = [
            f
            for f in findings
            if "mid" in f["code"].lower() or "sentence" in f["message"].lower()
        ]
        self.assertEqual(mid, [], msg=f"table should not mid-sentence flag: {findings}")

    def test_does_not_flag_bullet_list_ending_without_period(self) -> None:
        draft = _draft(
            _section(
                "Deliverables include:\n"
                "- Discovery workshop\n"
                "- Creative concepts\n"
                "- Media plan"
            )
        )
        findings = scan_truncation_artifacts(draft)
        mid = [
            f
            for f in findings
            if "mid" in f["code"].lower() or "sentence" in f["message"].lower()
        ]
        self.assertEqual(mid, [], msg=f"bullets should not mid-sentence flag: {findings}")

    def test_does_not_flag_numbered_list_ending(self) -> None:
        draft = _draft(
            _section(
                "Phases:\n"
                "1. Discovery\n"
                "2. Strategy\n"
                "3. Implementation"
            )
        )
        findings = scan_truncation_artifacts(draft)
        mid = [
            f
            for f in findings
            if "mid" in f["code"].lower() or "sentence" in f["message"].lower()
        ]
        self.assertEqual(mid, [])

    def test_does_not_flag_heading_ending_section(self) -> None:
        draft = _draft(
            _section(
                "We outline the approach below.\n"
                "## Appendix Materials"
            )
        )
        findings = scan_truncation_artifacts(draft)
        mid = [
            f
            for f in findings
            if "mid" in f["code"].lower() or "sentence" in f["message"].lower()
        ]
        self.assertEqual(mid, [])

    def test_flags_unbalanced_parentheses(self) -> None:
        draft = _draft(
            _section("The fee schedule (see Appendix A includes all pass-throughs.")
        )
        findings = scan_truncation_artifacts(draft)
        self.assertTrue(
            any(
                "paren" in f["code"].lower()
                or "paren" in f["message"].lower()
                or "delimiter" in f["message"].lower()
                or "unbalanced" in f["message"].lower()
                for f in findings
            ),
            msg=f"expected unbalanced paren finding, got {findings}",
        )

    def test_does_not_flag_balanced_parens_and_verify_brackets(self) -> None:
        draft = _draft(
            _section(
                "Fee (agency) is listed. Staffing: [VERIFY: confirm FTE]. "
                "Address: [MANUAL FILL]."
            )
        )
        findings = scan_truncation_artifacts(draft)
        unbalanced = [
            f
            for f in findings
            if "unbalanced" in f["message"].lower()
            or "paren" in f["code"].lower()
            or "bracket" in f["code"].lower()
        ]
        self.assertEqual(unbalanced, [])

    def test_empty_section_no_findings(self) -> None:
        draft = _draft(_section(""))
        findings = scan_truncation_artifacts(draft)
        self.assertEqual(findings, [])

    def test_short_complete_sentence_no_findings(self) -> None:
        draft = _draft(_section("We are ready."))
        findings = scan_truncation_artifacts(draft)
        self.assertEqual(findings, [])


class ScanAllAndBlockerMessagesTests(unittest.TestCase):
    def test_scan_all_combines_note_and_truncation(self) -> None:
        draft = _draft(
            _section(
                "[FLAG FOR SONJA: fix]\n"
                "Full resumes and bio summaries for each named",
                section_id="s9",
            )
        )
        findings = scan_all_t1(draft)
        categories = {f["category"] for f in findings}
        self.assertIn("note_leak", categories)
        self.assertIn("truncation", categories)

    def test_blocker_messages_only_include_blockers(self) -> None:
        draft = _draft(
            _section("[FLAG FOR TEAM: review]", section_id="s2", title="Risks")
        )
        findings = scan_all_t1(draft)
        messages = t1_findings_as_blocker_messages(findings)
        self.assertTrue(messages)
        self.assertTrue(all(isinstance(m, str) and m for m in messages))
        # Non-blocker findings (if any) must be excluded
        for f in findings:
            if not f["blocker"]:
                self.assertFalse(
                    any(f["code"] in m or f["message"] in m for m in messages)
                )

    def test_blocker_messages_empty_when_no_blockers(self) -> None:
        draft = _draft(_section("Clean complete sentence."))
        findings = scan_all_t1(draft)
        self.assertEqual(t1_findings_as_blocker_messages(findings), [])


if __name__ == "__main__":
    unittest.main()
