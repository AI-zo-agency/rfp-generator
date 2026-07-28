"""Tests for proposal chat ops (duplicates + fabrication purge)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_chat_ops import (
    apply_duplicate_removals,
    audit_duplicates,
    classify_chat_op,
    format_duplicate_report,
)


def _section(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        status="generated",
        source="template",
    )


class ClassifyChatOpTests(unittest.TestCase):
    def test_check_duplicates(self) -> None:
        self.assertEqual(classify_chat_op("check duplicates"), "check_duplicates")
        self.assertEqual(
            classify_chat_op("Are there any duplicate case studies?"),
            "check_duplicates",
        )

    def test_remove_duplicates(self) -> None:
        self.assertEqual(classify_chat_op("remove duplicates"), "remove_duplicates")
        self.assertEqual(
            classify_chat_op("Please dedupe repeated paragraphs"),
            "remove_duplicates",
        )

    def test_remove_fabricated(self) -> None:
        self.assertEqual(
            classify_chat_op("remove fabricated things"),
            "remove_fabricated",
        )
        self.assertEqual(
            classify_chat_op("strip invented references and hallucinations"),
            "remove_fabricated",
        )

    def test_none(self) -> None:
        self.assertEqual(classify_chat_op("make this warmer"), "none")


def _draft(sections: list[ProposalSection]) -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        updatedAt="2026-07-22T00:00:00Z",
        sections=sections,
    )


class DuplicateAuditTests(unittest.TestCase):
    def test_finds_duplicate_case_study_clients(self) -> None:
        draft = _draft(
            [
                _section(
                    "section-3-work-1",
                    "3.1 — Deschutes Brewery",
                    "Case study about beer branding and campaigns with results.",
                ),
                _section(
                    "section-3-work-2",
                    "3.2 — Deschutes Brewery",
                    "Another card for the same brewery with more words here about KPIs.",
                ),
                _section(
                    "section-1-who",
                    "Who We Are",
                    "zö agency is a woman-owned firm based in Bend.",
                ),
            ]
        )
        findings = audit_duplicates(draft)
        kinds = {f.kind for f in findings}
        self.assertIn("case_study_client", kinds)

    def test_finds_near_duplicate_prose(self) -> None:
        para = (
            "In today's competitive municipal marketplace, agencies must demonstrate "
            "proven website modernization experience with measurable citizen engagement "
            "outcomes and accessible design systems that serve diverse communities."
        )
        draft = _draft(
            [
                _section("a", "Approach", para + " Extra unique approach detail one."),
                _section("b", "Methodology", para + " Extra unique method detail two."),
            ]
        )
        findings = audit_duplicates(draft)
        self.assertTrue(
            any(f.kind in {"near_duplicate_prose", "repeated_opener"} for f in findings),
            findings,
        )

    def test_remove_duplicate_case_studies(self) -> None:
        draft = _draft(
            [
                _section(
                    "section-3-work-1",
                    "3.1 — Medford",
                    "Short.",
                ),
                _section(
                    "section-3-work-2",
                    "3.2 — Medford",
                    "Much longer case study body with plenty of detail about the brand work.",
                ),
            ]
        )
        findings = audit_duplicates(draft)
        updated, logs = apply_duplicate_removals(draft, findings)
        s3 = [
            s
            for s in updated.sections
            if s.id.startswith("section-3-work-")
        ]
        self.assertEqual(len(s3), 1)
        self.assertIn("longer", (s3[0].content or "").casefold())
        self.assertTrue(logs)

    def test_report_format(self) -> None:
        draft = _draft(
            [
                _section(
                    "section-3-work-1",
                    "3.1 — X Client",
                    "Body one with enough characters for a real case study paragraph here.",
                ),
                _section(
                    "section-3-work-2",
                    "3.2 — X Client",
                    "Body two with enough characters for a real case study paragraph here.",
                ),
            ]
        )
        findings = audit_duplicates(draft)
        report = format_duplicate_report(findings, acted=False)
        self.assertIn("Duplicate check", report)


if __name__ == "__main__":
    unittest.main()
