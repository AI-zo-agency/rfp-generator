"""Agentic QC repair — selection + resume-tab preservation."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_agentic_qc_repair import (
    build_bio_toc_lines,
    detect_qc_defect_reasons,
    section_is_resume_pointer_tab,
)


def _sec(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        status="generated",
    )


class AgenticQcSelectionTests(unittest.TestCase):
    def test_flags_bio_verify_placeholder(self) -> None:
        sec = _sec(
            "resumes",
            "Resumes of Key Personnel",
            (
                "See [VERIFY: Sonja — confirm actual RFP section citation for this "
                "requirement — manuscript bio §2.1 (Sonja Anderson) was incorrectly "
                "substituted] for this narrative."
            ),
        )
        self.assertTrue(section_is_resume_pointer_tab(sec))
        reasons = detect_qc_defect_reasons(sec)
        self.assertIn("bio_verify_placeholder", reasons)

    def test_flags_fabricated_signed_and_truncated(self) -> None:
        signed = _sec(
            "s21",
            "Required Submittals",
            "Exhibit J is included as a completed, signed, and dated attachment.",
        )
        self.assertIn("fabricated_signed_claim", detect_qc_defect_reasons(signed))
        trunc = _sec(
            "s39",
            "Exhibit F",
            "☐ The proposing firm is itself a California-certified Disabled .",
        )
        self.assertIn("truncated_sentence", detect_qc_defect_reasons(trunc))

    def test_flags_empty_exhibit_status_cells(self) -> None:
        from app.services.proposal_agentic_qc_repair import (
            exhibit_checklist_has_empty_status,
        )

        table = (
            "The following required exhibits are included:\n\n"
            "| Exhibit | Description | Status |\n"
            "| --- | --- | --- |\n"
            "| Vendor Supplied Proposal | Full packet | Included |\n"
            "| Exhibit D | Bidder Declaration | |\n"
            "| Exhibit J | Proposal Certification Form | |\n"
            "| Exhibit K | References Form | |\n"
            "| Exhibit L (Pricing) | Cost proposal | Submitted as separate electronic file |\n"
        )
        self.assertTrue(exhibit_checklist_has_empty_status(table))
        sec = _sec("vendor", "Vendor Supplied Proposal", table)
        self.assertIn("empty_exhibit_status", detect_qc_defect_reasons(sec))

    def test_bio_toc_from_section_2(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                _sec("section-2-bio-sonja", "2.1 — Sonja Anderson", "Bio."),
                _sec("section-2-bio-todd", "2.2 — Todd Anderson", "Bio."),
            ],
        )
        toc = build_bio_toc_lines(draft)
        self.assertEqual(toc[0], "§2.1 — Sonja Anderson")
        self.assertEqual(toc[1], "§2.2 — Todd Anderson")


class AgenticQcSkipsCostSectionTests(unittest.TestCase):
    def test_skip_section_ids_never_rewritten(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.services import proposal_agentic_qc_repair as qc

        cost = _sec("section-budget-pricing", "Budget & Pricing", "Fees cover the Disabled .")
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[cost])
        self.assertTrue(detect_qc_defect_reasons(cost))
        rewrite = AsyncMock(return_value="rewritten cost text")
        with patch.object(qc.llm, "is_configured", return_value=True), patch.object(
            qc, "_agent_rewrite_section", new=rewrite
        ):
            out, logs = asyncio.run(
                qc.run_agentic_manuscript_qc_repair(
                    draft, skip_section_ids={"section-budget-pricing"}
                )
            )
        rewrite.assert_not_awaited()
        self.assertEqual(out.sections[0].content, cost.content)
        self.assertEqual(logs, [])

    def test_persist_guard_passes_cost_section_as_skip(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.services.proposal_zero_fabrication import (
            apply_zero_fabrication_guards_before_persist,
        )

        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                _sec("s1", "Approach", "We will do the work."),
                _sec("section-budget-pricing", "Budget & Pricing", "| Fees | $1 |"),
            ],
        )

        async def _fake(d, **kwargs):
            return d, []

        qc_mock = AsyncMock(side_effect=_fake)
        with patch(
            "app.services.proposal_agentic_qc_repair.run_agentic_manuscript_qc_repair",
            new=qc_mock,
        ):
            asyncio.run(apply_zero_fabrication_guards_before_persist(draft))
        qc_mock.assert_awaited_once()
        self.assertEqual(
            qc_mock.await_args.kwargs.get("skip_section_ids"), {"section-budget-pricing"}
        )


if __name__ == "__main__":
    unittest.main()
