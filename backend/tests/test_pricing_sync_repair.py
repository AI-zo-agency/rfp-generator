"""Pricing sync report model + manual-fill handoff helpers."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    PricingSyncReport,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_adversarial_repair import (
    append_manual_fill_tag,
    ensure_open_pricing_handoffs_section,
)


class PricingSyncReportModelTests(unittest.TestCase):
    def test_pricing_sync_report_round_trip(self) -> None:
        report = PricingSyncReport(
            roundsRun=2,
            resolved=False,
            handoff=True,
            mismatchCount=2,
            codes=["budget_grounding_rfp_authority"],
            samples=["over envelope"],
        )
        research = ProposalResearchCache(
            rfpId="rfp-1",
            pricingSyncReport=report,
            updatedAt="2026-08-01T00:00:00Z",
        )
        dumped = research.model_dump(by_alias=True)
        restored = ProposalResearchCache.model_validate(dumped)
        assert restored.pricing_sync_report is not None
        self.assertEqual(restored.pricing_sync_report.rounds_run, 2)
        self.assertTrue(restored.pricing_sync_report.handoff)
        self.assertEqual(
            restored.pricing_sync_report.codes,
            ["budget_grounding_rfp_authority"],
        )


class ManualFillExportTests(unittest.TestCase):
    def test_append_manual_fill_tag_code_first(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[
                ProposalSection(
                    id="pricing",
                    title="Pricing Structure",
                    content="Agency fee narrative.",
                    status="generated",
                    source="generated",
                    mode="write",
                )
            ],
            updatedAt="2026-08-01T00:00:00Z",
            generatedAt="2026-08-01T00:00:00Z",
        )
        updated, tag = append_manual_fill_tag(
            draft,
            section_id="pricing",
            issue="Labeled agency_fee claim does not match canonical",
            finding_code="budget_grounding_agency_fee",
        )
        self.assertIsNotNone(tag)
        assert tag is not None
        self.assertIn("budget_grounding_agency_fee", tag)
        self.assertIn("[MANUAL FILL:", tag)
        self.assertIn(tag, updated.sections[0].content or "")

    def test_ensure_open_pricing_handoffs_section_idempotent(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-1",
            sections=[],
            updatedAt="2026-08-01T00:00:00Z",
            generatedAt="2026-08-01T00:00:00Z",
        )
        d1, sid1 = ensure_open_pricing_handoffs_section(draft)
        d2, sid2 = ensure_open_pricing_handoffs_section(d1)
        self.assertEqual(sid1, sid2)
        self.assertEqual(
            sum(1 for s in d2.sections if s.id == sid1),
            1,
        )


if __name__ == "__main__":
    unittest.main()
