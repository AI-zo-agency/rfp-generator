"""Pipeline checkpoints — record progress and resume after errors."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from app.models.proposal import (
    ProposalDraft,
    ProposalPipelineCheckpoint,
    ProposalResearchCache,
)
from app.services.proposal_pipeline_status import count_verify_tags
from app.services.proposal_repository import get_research_cache, save_research_cache

logger = logging.getLogger(__name__)

PIPELINE_PHASES: tuple[str, ...] = (
    "sections-1-3",
    "phase-2",
    "phase-3",
    "phase-3-6-self-edit",
    "phase-3-5-budget",
    "phase-4-review",
)

PHASE_LABELS: dict[str, str] = {
    "sections-1-3": "Sections 1–3",
    "phase-2": "Phase 2 research",
    "phase-3": "Phase 3 drafting",
    "phase-3-6-self-edit": "Senior editor polish",
    "phase-3-5-budget": "Budget build",
    "phase-4-review": "Pre-submit review",
    "complete": "Complete",
}


def _phase_index(phase: str) -> int:
    if phase == "complete":
        return len(PIPELINE_PHASES)
    try:
        return PIPELINE_PHASES.index(phase)
    except ValueError:
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_research(rfp_id: str) -> ProposalResearchCache:
    research = get_research_cache(rfp_id)
    if research:
        return research
    now = _now_iso()
    return ProposalResearchCache(rfpId=rfp_id, updatedAt=now)


def _save_checkpoint(rfp_id: str, checkpoint: ProposalPipelineCheckpoint) -> ProposalResearchCache:
    research = _ensure_research(rfp_id)
    updated = research.model_copy(
        update={
            "pipeline_checkpoint": checkpoint,
            "updated_at": _now_iso(),
        }
    )
    save_research_cache(updated)
    return updated


def _checkpoint_age_sec(checkpoint: ProposalPipelineCheckpoint) -> float | None:
    try:
        updated = datetime.fromisoformat(checkpoint.updated_at.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds()
    except (TypeError, ValueError):
        return None


_IN_PROGRESS_STALE_SEC = 600


def clear_stale_in_progress_checkpoint(rfp_id: str) -> bool:
    """Mark abandoned in-progress phases failed after server kill or disconnect."""
    research = get_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return False
    cp = research.pipeline_checkpoint
    if not cp.in_progress_phase:
        return False
    age = _checkpoint_age_sec(cp)
    if age is None or age < _IN_PROGRESS_STALE_SEC:
        return False
    record_phase_failed(
        rfp_id,
        cp.in_progress_phase,
        "Phase interrupted (server or connection lost). Resume to retry.",
    )
    logger.warning(
        "Pipeline checkpoint: %s cleared stale in-progress %s (age=%.0fs)",
        rfp_id,
        cp.in_progress_phase,
        age,
    )
    return True


def record_phase_started(rfp_id: str, phase: str) -> None:
    clear_stale_in_progress_checkpoint(rfp_id)
    research = _ensure_research(rfp_id)
    prior = research.pipeline_checkpoint
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=prior.last_completed_phase if prior else None,
        inProgressPhase=phase,
        lastFailedPhase=None,
        lastError=None,
        resumeFromPhase=phase,
        updatedAt=_now_iso(),
    )
    _save_checkpoint(rfp_id, checkpoint)
    logger.info("Pipeline checkpoint: %s started %s", rfp_id, phase)


def _next_phase_after(completed_phase: str) -> str:
    idx = _phase_index(completed_phase)
    if idx + 1 >= len(PIPELINE_PHASES):
        return "complete"
    return PIPELINE_PHASES[idx + 1]


def record_phase_completed(rfp_id: str, phase: str) -> None:
    if phase == "phase-3-6-self-edit":
        from app.services.proposal_repository import get_proposal_draft

        draft = get_proposal_draft(rfp_id)
        if draft:
            remaining = count_verify_tags(draft)
            if remaining > 0:
                record_phase_failed(
                    rfp_id,
                    phase,
                    f"{remaining} unresolved [VERIFY] placeholder(s) remain after self-edit",
                )
                return

    next_phase = _next_phase_after(phase)
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=phase,
        inProgressPhase=None,
        lastFailedPhase=None,
        lastError=None,
        resumeFromPhase=None if next_phase == "complete" else next_phase,
        updatedAt=_now_iso(),
    )
    _save_checkpoint(rfp_id, checkpoint)
    logger.info("Pipeline checkpoint: %s completed %s (next=%s)", rfp_id, phase, next_phase)


def record_phase_failed(rfp_id: str, phase: str, error: str) -> None:
    research = get_research_cache(rfp_id)
    prior = research.pipeline_checkpoint if research else None
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=prior.last_completed_phase if prior else None,
        inProgressPhase=None,
        lastFailedPhase=phase,
        lastError=error[:2000] if error else None,
        resumeFromPhase=phase,
        updatedAt=_now_iso(),
    )
    _save_checkpoint(rfp_id, checkpoint)
    logger.warning("Pipeline checkpoint: %s failed at %s — %s", rfp_id, phase, error[:200])


def clear_pipeline_checkpoint(rfp_id: str) -> None:
    research = get_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return
    updated = research.model_copy(
        update={"pipeline_checkpoint": None, "updated_at": _now_iso()}
    )
    save_research_cache(updated)


@asynccontextmanager
async def pipeline_phase(rfp_id: str, phase: str):
    record_phase_started(rfp_id, phase)
    try:
        yield
        record_phase_completed(rfp_id, phase)
    except Exception as exc:
        record_phase_failed(rfp_id, phase, str(exc))
        raise


def phase_is_complete(
    *,
    draft: ProposalDraft | None,
    research: ProposalResearchCache | None,
    phase: str,
) -> bool:
    if phase == "sections-1-3":
        if not draft or len(draft.sections) < 3:
            return False
        return all(section.content.strip() for section in draft.sections[:3])

    if not research:
        return False

    if phase == "phase-2":
        return bool(research.evidence_corpus and research.rfp_sections)

    if phase == "phase-3":
        if not draft or not research.rfp_sections:
            return False
        mapped_ids = {section.id for section in research.rfp_sections}
        if not mapped_ids:
            return False
        filled = sum(
            1
            for section in draft.sections
            if section.id in mapped_ids and section.content.strip()
        )
        return filled >= max(1, int(len(mapped_ids) * 0.85))

    if phase == "phase-3-6-self-edit":
        if draft and count_verify_tags(draft) > 0:
            return False
        cp = research.pipeline_checkpoint
        if cp and cp.last_failed_phase == phase:
            return False
        if cp and cp.last_completed_phase:
            return _phase_index(cp.last_completed_phase) >= _phase_index(phase)
        return False

    if phase == "phase-3-5-budget":
        return research.budget is not None

    if phase == "phase-4-review":
        return research.presubmit_review is not None

    return False


def resolve_resume_phase(
    rfp_id: str,
    *,
    draft: ProposalDraft | None,
    research: ProposalResearchCache | None,
) -> str:
    if draft is None:
        draft = None  # caller may pass None; infer from research only for early phases
    from app.services.proposal_repository import get_proposal_draft

    if draft is None:
        draft = get_proposal_draft(rfp_id)
    if research is None:
        research = get_research_cache(rfp_id)

    cp = research.pipeline_checkpoint if research else None
    if cp:
        if cp.last_failed_phase and cp.last_failed_phase in PIPELINE_PHASES:
            return cp.last_failed_phase
        if cp.in_progress_phase and cp.in_progress_phase in PIPELINE_PHASES:
            return cp.in_progress_phase
        if cp.resume_from_phase and cp.resume_from_phase in PIPELINE_PHASES:
            if not phase_is_complete(draft=draft, research=research, phase=cp.resume_from_phase):
                return cp.resume_from_phase

    for phase in PIPELINE_PHASES:
        if not phase_is_complete(draft=draft, research=research, phase=phase):
            return phase
    if draft and research:
        if count_verify_tags(draft) > 0:
            return "phase-3-6-self-edit"
        if not research.presubmit_review:
            return "phase-4-review"
        if not research.proof_points:
            return "phase-2"
    return "complete"


def build_pipeline_status(
    rfp_id: str,
    *,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> dict[str, object]:
    from app.services.proposal_repository import get_proposal_draft

    if draft is None:
        draft = get_proposal_draft(rfp_id)
    if research is None:
        research = get_research_cache(rfp_id)

    clear_stale_in_progress_checkpoint(rfp_id)
    if research is None:
        research = get_research_cache(rfp_id)

    resume_from = resolve_resume_phase(rfp_id, draft=draft, research=research)
    completed = [
        phase
        for phase in PIPELINE_PHASES
        if phase_is_complete(draft=draft, research=research, phase=phase)
    ]
    cp = research.pipeline_checkpoint if research else None
    return {
        "resumeFromPhase": resume_from,
        "completedPhases": completed,
        "isComplete": resume_from == "complete",
        "canResume": bool(draft)
        and (
            (cp is not None and cp.last_failed_phase is not None)
            or resume_from != "complete"
        ),
        "lastCompletedPhase": cp.last_completed_phase if cp else (completed[-1] if completed else None),
        "lastFailedPhase": cp.last_failed_phase if cp else None,
        "lastError": cp.last_error if cp else None,
        "inProgressPhase": cp.in_progress_phase if cp else None,
        "phaseLabels": PHASE_LABELS,
        "checkpoint": cp.model_dump(by_alias=True) if cp else None,
    }
