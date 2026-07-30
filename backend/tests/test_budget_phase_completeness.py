"""Budget phase completeness after hard failures."""

from app.models.proposal import (
    ProposalBudget,
    ProposalDraft,
    ProposalPipelineCheckpoint,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_pipeline_checkpoint import phase_is_complete


def _research_with_budget(*, last_failed: str | None, last_error: str | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-01T00:00:00Z",
        budget=ProposalBudget(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            agencyRevenueEstimate=1000.0,
            lineItems=[],
        ),
        pipelineCheckpoint=ProposalPipelineCheckpoint(
            lastCompletedPhase=None,
            inProgressPhase=None,
            lastFailedPhase=last_failed,
            lastError=last_error,
            resumeFromPhase=last_failed,
            updatedAt="2026-01-01T00:00:00Z",
        ),
    )


def test_budget_present_alone_is_complete() -> None:
    research = _research_with_budget(last_failed=None, last_error=None)
    assert phase_is_complete(
        draft=ProposalDraft(rfpId="r1", updatedAt="t", sections=[]),
        research=research,
        phase="phase-3-5-budget",
    )


def test_budget_present_but_grounding_failed_is_not_complete() -> None:
    research = _research_with_budget(
        last_failed="phase-3-5-budget",
        last_error=(
            "Budget grounding check found unresolved pricing contradictions. "
            "Resolve pricing mismatches before senior editor / Phase 4."
        ),
    )
    assert not phase_is_complete(
        draft=ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[ProposalSection(id="s", title="Cost", content="x", status="generated")],
        ),
        research=research,
        phase="phase-3-5-budget",
    )
