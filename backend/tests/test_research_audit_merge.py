"""Tests for preserving adversarial audit/repair on research save merge."""

from app.models.proposal import (
    AdversarialRepairReport,
    PricingSyncReport,
    ProposalAdversarialAudit,
    ProposalResearchCache,
)
from app.services.proposal_research_merge import merge_research_preserve_audit_fields


def test_merge_preserves_audit_when_incoming_none() -> None:
    existing = ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-01T00:00:00Z",
        adversarialAudit=ProposalAdversarialAudit(
            rfpId="r1",
            findings=[],
            summary="ok",
            scannedAt="2026-01-01T00:00:00Z",
            provider="deterministic",
        ),
        adversarialRepairReport=AdversarialRepairReport(
            roundsRun=2,
            stoppedReason="resolved",
            resolved=True,
        ),
    )
    incoming = ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-02T00:00:00Z",
        writingAvoidances=["keep me"],
    )
    merged = merge_research_preserve_audit_fields(incoming, existing)
    assert merged.adversarial_audit is not None
    assert merged.adversarial_repair_report is not None
    assert merged.adversarial_repair_report.rounds_run == 2
    assert merged.writing_avoidances == ["keep me"]


def test_merge_preserves_pricing_sync_report_when_incoming_none() -> None:
    existing = ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-01T00:00:00Z",
        pricingSyncReport=PricingSyncReport(
            roundsRun=2,
            resolved=False,
            handoff=True,
            mismatchCount=3,
            codes=["budget_grounding_agency_fee"],
            samples=["Agency fee mismatch"],
        ),
    )
    incoming = ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-02T00:00:00Z",
        writingAvoidances=["keep me"],
    )
    merged = merge_research_preserve_audit_fields(incoming, existing)
    assert merged.pricing_sync_report is not None
    assert merged.pricing_sync_report.rounds_run == 2
    assert merged.pricing_sync_report.handoff is True
    assert merged.writing_avoidances == ["keep me"]


def test_merge_does_not_overwrite_incoming_audit() -> None:
    existing = ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-01T00:00:00Z",
        adversarialAudit=ProposalAdversarialAudit(
            rfpId="r1",
            findings=[],
            summary="old",
            scannedAt="2026-01-01T00:00:00Z",
            provider="deterministic",
        ),
    )
    incoming = ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-02T00:00:00Z",
        adversarialAudit=ProposalAdversarialAudit(
            rfpId="r1",
            findings=[],
            summary="new",
            scannedAt="2026-01-02T00:00:00Z",
            provider="fireworks",
        ),
    )
    merged = merge_research_preserve_audit_fields(incoming, existing)
    assert merged.adversarial_audit is not None
    assert merged.adversarial_audit.summary == "new"
