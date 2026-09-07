"""Deterministic Table of Contents fill from the live manuscript."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_table_of_contents import (
    build_manuscript_toc_markdown,
    fill_table_of_contents_in_draft,
    is_table_of_contents_section,
)


class TocDetectTests(unittest.TestCase):
    def test_detects_toc_titles(self) -> None:
        self.assertTrue(
            is_table_of_contents_section(
                ProposalSection(id="t", title="5. Table of Contents", content="")
            )
        )
        self.assertFalse(
            is_table_of_contents_section(
                ProposalSection(id="e", title="Executive Summary", content="")
            )
        )


class TocBuildTests(unittest.TestCase):
    def test_builds_section_page_table_excluding_toc_itself(self) -> None:
        draft = ProposalDraft(
            rfp_id="r1",
            updatedAt="2026-09-07T00:00:00Z",
            sections=[
                ProposalSection(id="cover", title="Cover Letter", content="Dear City,"),
                ProposalSection(
                    id="toc",
                    title="Table of Contents",
                    content="[MANUAL FILL: Draft this RFP-required section — Table of Contents]",
                ),
                ProposalSection(
                    id="exec",
                    title="Executive Summary",
                    content="We lock one calendar.",
                ),
                ProposalSection(id="fee", title="Fee Proposal", content="Total $1."),
            ],
        )
        md = build_manuscript_toc_markdown(draft)
        self.assertIn("| Section | Page |", md)
        self.assertIn("Cover Letter", md)
        self.assertIn("Executive Summary", md)
        self.assertIn("Fee Proposal", md)
        self.assertNotIn("| Table of Contents |", md)
        self.assertIn("DESIGNER NOTE", md)

    def test_toc_markdown_has_no_em_dash(self) -> None:
        draft = ProposalDraft(
            rfp_id="r1",
            updatedAt="2026-09-07T00:00:00Z",
            sections=[
                ProposalSection(id="a", title="Staffing", content="Ella leads."),
                ProposalSection(id="toc", title="Table of Contents", content=""),
            ],
        )
        md = build_manuscript_toc_markdown(draft)
        self.assertNotIn("—", md)
        self.assertNotIn("–", md)

    def test_fill_replaces_hollow_toc_body(self) -> None:
        draft = ProposalDraft(
            rfp_id="r1",
            updatedAt="2026-09-07T00:00:00Z",
            sections=[
                ProposalSection(id="a", title="Staffing", content="Ella leads."),
                ProposalSection(
                    id="toc",
                    title="2. Table of Contents",
                    content="[MANUAL FILL: Draft this RFP-required section — TOC]",
                ),
                ProposalSection(id="b", title="Insurance", content="COI attached."),
            ],
        )
        out, logs = fill_table_of_contents_in_draft(draft)
        toc = next(s for s in out.sections if s.id == "toc")
        self.assertIn("| Section | Page |", toc.content or "")
        self.assertIn("Staffing", toc.content or "")
        self.assertIn("Insurance", toc.content or "")
        self.assertNotIn("MANUAL FILL: Draft this RFP-required", toc.content or "")
        self.assertTrue(any("Table of Contents" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
