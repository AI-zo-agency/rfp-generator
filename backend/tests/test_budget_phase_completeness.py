"""Budget / review phase completeness — artifacts alone must not skip phases."""

from app.models.proposal import (
    ProposalBudget,
    ProposalDraft,
    ProposalPipelineCheckpoint,
    ProposalResearchCache,
    ProposalSection,
    PreSubmitReview,
)
from app.services.proposal_pipeline_checkpoint import phase_is_complete


def _research(
    *,
    last_completed: str | None,
    last_failed: str | None = None,
    last_error: str | None = None,
    with_budget: bool = True,
    with_review: bool = False,
) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-01-01T00:00:00Z",
        budget=(
            ProposalBudget(
                rfpId="r1",
                updatedAt="2026-01-01T00:00:00Z",
                agencyRevenueEstimate=1000.0,
                lineItems=[],
            )
            if with_budget
            else None
        ),
        presubmitReview=(
            PreSubmitReview(
                rfpId="r1",
                issues=[],
                readyToSubmit=False,
                scannedAt="2026-01-01T00:00:00Z",
            )
            if with_review
            else None
        ),
        pipelineCheckpoint=ProposalPipelineCheckpoint(
            lastCompletedPhase=last_completed,
            inProgressPhase=None,
            lastFailedPhase=last_failed,
            lastError=last_error,
            resumeFromPhase=last_failed or last_completed,
            updatedAt="2026-01-01T00:00:00Z",
        ),
    )


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        updatedAt="t",
        sections=[ProposalSection(id="s", title="Cost", content="x", status="generated")],
    )


def test_budget_artifact_without_checkpoint_is_not_complete() -> None:
    research = _research(last_completed="phase-3", with_budget=True)
    assert not phase_is_complete(
        draft=_draft(),
        research=research,
        phase="phase-3-5-budget",
    )


def test_budget_complete_when_checkpoint_reached() -> None:
    research = _research(last_completed="phase-3-5-budget", with_budget=True)
    assert phase_is_complete(
        draft=_draft(),
        research=research,
        phase="phase-3-5-budget",
    )


def test_budget_present_but_grounding_failed_is_not_complete() -> None:
    research = _research(
        last_completed="phase-3-5-budget",
        last_failed="phase-3-5-budget",
        last_error=(
            "Budget grounding check found unresolved pricing contradictions. "
            "Resolve pricing mismatches before senior editor / Phase 4."
        ),
        with_budget=True,
    )
    assert not phase_is_complete(
        draft=_draft(),
        research=research,
        phase="phase-3-5-budget",
    )


def test_stale_review_after_self_edit_is_not_complete() -> None:
    """Matches the AHEC stuck state: self-edit done, old review still in cache."""
    research = _research(
        last_completed="phase-3-6-self-edit",
        with_budget=True,
        with_review=True,
    )
    assert not phase_is_complete(
        draft=_draft(),
        research=research,
        phase="phase-4-review",
    )


def test_review_complete_when_checkpoint_reached() -> None:
    research = _research(
        last_completed="phase-4-review",
        with_budget=True,
        with_review=True,
    )
    assert phase_is_complete(
        draft=_draft(),
        research=research,
        phase="phase-4-review",
    )
