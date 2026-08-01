"""Phase 3.5 pricing sync retry → handoff."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    BudgetNarrativeMismatch,
    PricingSyncReport,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_adversarial_repair import (
    append_manual_fill_tag,
    ensure_open_pricing_handoffs_section,
)
from app.services.proposal_pricing_sync_repair import (
    MAX_HANDOFF_TAGS,
    grounding_code_for_mismatch,
    run_pricing_sync_repair_or_handoff,
    scrub_invented_ceiling_claims,
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


def _fee_budget(fee: float = 100_000.0, media: float = 30_000.0) -> ProposalBudget:
    total = fee + media
    return ProposalBudget(
        rfpId="fixture-sync",
        updatedAt="2026-08-01T00:00:00Z",
        lineItems=[
            BudgetLineItem(
                id="L1",
                category="Strategy",
                description="Fees",
                extended=fee,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="M1",
                category="Media",
                description="Media",
                extended=media,
                lineItemType="client_passthrough",
            ),
        ],
        agencyRevenueEstimate=fee,
        agencyFeeSubtotal=fee,
        clientMediaPassthrough=media,
        totalClientInvoicing=total,
        lineItemSum=total,
        lumpSumTotal=fee,
    )


class InventedCeilingScrubTests(unittest.TestCase):
    def test_scrubs_bid_labeled_as_rfp_ceiling(self) -> None:
        budget = _fee_budget()
        draft = ProposalDraft(
            rfpId="fixture-sync",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content=(
                        "We propose a thoughtful campaign. "
                        "The RFP ceiling is $130,000 for this engagement. "
                        "Next steps follow."
                    ),
                    status="generated",
                    source="generated",
                    mode="write",
                )
            ],
            updatedAt="2026-08-01T00:00:00Z",
            generatedAt="2026-08-01T00:00:00Z",
        )
        updated, n = scrub_invented_ceiling_claims(draft, budget)
        self.assertGreaterEqual(n, 1)
        body = updated.sections[0].content or ""
        self.assertNotIn("RFP ceiling is $130,000", body)
        self.assertIn("thoughtful campaign", body)

    def test_grounding_code_mapping(self) -> None:
        self.assertEqual(
            grounding_code_for_mismatch(
                BudgetNarrativeMismatch(
                    sectionId="s1",
                    sentence="x",
                    claimedField="agency_fee",
                    matches=False,
                )
            ),
            "budget_grounding_agency_fee",
        )
        self.assertEqual(
            grounding_code_for_mismatch(
                BudgetNarrativeMismatch(
                    sectionId="budget",
                    sentence="over",
                    claimedField="rfp_authority",
                    matches=False,
                )
            ),
            "budget_grounding_rfp_authority",
        )
        self.assertEqual(
            grounding_code_for_mismatch(
                BudgetNarrativeMismatch(
                    sectionId="s1",
                    sentence="ceil",
                    claimedField="rfp_ceiling_claim",
                    matches=False,
                )
            ),
            "budget_grounding_invented_ceiling",
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


class PricingSyncRepairLoopTests(unittest.TestCase):
    def _draft(self) -> ProposalDraft:
        return ProposalDraft(
            rfpId="fixture-sync",
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

    def _mismatch(
        self,
        *,
        section_id: str = "pricing",
        claimed_field: str = "agency_fee",
        sentence: str = "Agency fee is $99,000.",
    ) -> BudgetNarrativeMismatch:
        return BudgetNarrativeMismatch(
            sectionId=section_id,
            sentence=sentence,
            claimedField=claimed_field,
            canonicalValue=100_000,
            matches=False,
        )

    def test_resolves_when_second_grounding_clean(self) -> None:
        draft = self._draft()
        budget = _fee_budget()
        research = ProposalResearchCache(
            rfpId="fixture-sync",
            budget=budget,
            updatedAt="2026-08-01T00:00:00Z",
        )

        with (
            patch(
                "app.services.proposal_pricing_sync_repair.rerender_budget_section_from_canon",
                side_effect=lambda draft, budget, **_: draft,
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.reconcile_draft_budget_summaries",
                side_effect=lambda draft, budget: (draft, 0),
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.align_fee_narrative_with_budget",
                new=AsyncMock(side_effect=lambda **kwargs: kwargs["draft"]),
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.scrub_invented_ceiling_claims",
                side_effect=lambda draft, budget: (draft, 0),
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.run_budget_grounding_check",
                new=AsyncMock(return_value=[]),
            ) as grounding_check,
        ):
            updated, updated_research, updated_budget, report = asyncio.run(
                run_pricing_sync_repair_or_handoff(
                    rfp_id="fixture-sync",
                    draft=draft,
                    budget=budget,
                    research=research,
                    initial_mismatches=[self._mismatch()],
                )
            )

        self.assertEqual(updated, draft)
        self.assertEqual(updated_budget.narrative_mismatches, [])
        self.assertTrue(report.resolved)
        self.assertFalse(report.handoff)
        self.assertEqual(report.rounds_run, 1)
        self.assertEqual(grounding_check.await_count, 1)
        assert updated_research is not None
        self.assertTrue(updated_research.pricing_sync_report.resolved)

    def test_handoff_on_rfp_authority_without_autoscale(self) -> None:
        draft = self._draft()
        budget = _fee_budget()
        leftovers = [
            self._mismatch(
                claimed_field="rfp_authority",
                sentence="The RFP permits only $100,000.",
            )
            for _ in range(MAX_HANDOFF_TAGS + 2)
        ]
        research = ProposalResearchCache(
            rfpId="fixture-sync",
            budget=budget,
            updatedAt="2026-08-01T00:00:00Z",
        )

        with (
            patch(
                "app.services.proposal_pricing_sync_repair.rerender_budget_section_from_canon",
                side_effect=lambda draft, budget, **_: draft,
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.reconcile_draft_budget_summaries",
                side_effect=lambda draft, budget: (draft, 0),
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.align_fee_narrative_with_budget",
                new=AsyncMock(side_effect=lambda **kwargs: kwargs["draft"]),
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.scrub_invented_ceiling_claims",
                side_effect=lambda draft, budget: (draft, 0),
            ),
            patch(
                "app.services.proposal_pricing_sync_repair.run_budget_grounding_check",
                new=AsyncMock(return_value=leftovers),
            ),
        ):
            updated, updated_research, updated_budget, report = asyncio.run(
                run_pricing_sync_repair_or_handoff(
                    rfp_id="fixture-sync",
                    draft=draft,
                    budget=budget,
                    research=research,
                    initial_mismatches=leftovers,
                )
            )

        self.assertEqual(updated_budget.agency_fee_subtotal, budget.agency_fee_subtotal)
        self.assertEqual(
            updated_budget.total_client_invoicing,
            budget.total_client_invoicing,
        )
        self.assertEqual(updated_budget.narrative_mismatches, leftovers)
        self.assertTrue(report.handoff)
        self.assertFalse(report.resolved)
        self.assertIn("budget_grounding_rfp_authority", report.codes)
        tags = (updated.sections[0].content or "").count("[MANUAL FILL:")
        self.assertLessEqual(tags, MAX_HANDOFF_TAGS)
        self.assertIn("budget_grounding_rfp_authority", updated.sections[0].content or "")
        assert updated_research is not None
        self.assertEqual(updated_research.pricing_sync_report.mismatch_count, len(leftovers))


class GeneratorGroundingWireTests(unittest.TestCase):
    def test_phase35_no_longer_raises_on_grounding_mismatch_doc(self) -> None:
        """Guard: generator must call repair helper instead of ProposalError text."""
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "app/services/proposal_generator.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("run_pricing_sync_repair_or_handoff", text)
        self.assertNotIn(
            "Resolve pricing mismatches before senior editor / Phase 4.",
            text,
        )
        self.assertNotIn(
            "Resolve pricing mismatches before continuing.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
