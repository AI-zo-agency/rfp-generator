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
from app.services.proposal_generator import static_sections_1_3_have_content
from app.services.proposal_pipeline_status import count_verify_tags
from app.services.proposal_repository import aget_research_cache, asave_research_cache

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
    "phase-2": "Phase 2 intelligence",
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


async def _ensure_research(rfp_id: str) -> ProposalResearchCache:
    research = await aget_research_cache(rfp_id)
    if research:
        return research
    now = _now_iso()
    return ProposalResearchCache(rfpId=rfp_id, updatedAt=now)


async def _save_checkpoint(rfp_id: str, checkpoint: ProposalPipelineCheckpoint) -> ProposalResearchCache:
    research = await _ensure_research(rfp_id)
    updated = research.model_copy(
        update={
            "pipeline_checkpoint": checkpoint,
            "updated_at": _now_iso(),
        }
    )
    await asave_research_cache(updated)
    return updated


def _checkpoint_age_sec(checkpoint: ProposalPipelineCheckpoint) -> float | None:
    try:
        updated = datetime.fromisoformat(checkpoint.updated_at.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds()
    except (TypeError, ValueError):
        return None


_IN_PROGRESS_STALE_SEC = 900  # RFP tabs can take many minutes per LLM call


def _iso_age_sec(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        updated = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds()
    except (TypeError, ValueError):
        return None


def _draft_recently_saved(draft: ProposalDraft | None, *, within_sec: float) -> bool:
    if not draft:
        return False
    age = _iso_age_sec(draft.updated_at)
    return age is not None and age < within_sec


def _self_edit_considered_complete(
    *,
    draft: ProposalDraft | None,
    research: ProposalResearchCache | None,
) -> bool:
    """Self-edit is done when checkpoint says so — leftover VERIFY/MANUAL FILL is handoff, not re-polish."""
    if not research:
        return False
    cp = research.pipeline_checkpoint
    if not cp:
        return False
    if cp.last_failed_phase == "phase-3-6-self-edit":
        err = (cp.last_error or "").lower()
        if "verify" in err or "placeholder" in err:
            return phase_is_complete(draft=draft, research=research, phase="phase-3")
    if cp.last_completed_phase:
        return _phase_index(cp.last_completed_phase) >= _phase_index("phase-3-6-self-edit")
    return False


async def clear_stale_in_progress_checkpoint(
    rfp_id: str,
    *,
    research: ProposalResearchCache | None = None,
    draft: ProposalDraft | None = None,
) -> bool:
    """Mark abandoned in-progress phases failed after server kill or disconnect."""
    if research is None:
        research = await aget_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return False
    cp = research.pipeline_checkpoint
    if not cp.in_progress_phase:
        return False

    from app.services.proposal_repository import aget_proposal_draft

    if draft is None:
        draft = await aget_proposal_draft(rfp_id)

    # Backend may still be drafting one tab for minutes — manuscript saves prove liveness.
    if _draft_recently_saved(draft, within_sec=_IN_PROGRESS_STALE_SEC):
        refreshed = cp.model_copy(update={"updated_at": _now_iso()})
        await _save_checkpoint(rfp_id, refreshed)
        return False

    age = _checkpoint_age_sec(cp)
    if age is None or age < _IN_PROGRESS_STALE_SEC:
        return False
    await record_phase_failed(
        rfp_id,
        cp.in_progress_phase,
        "Phase interrupted (connection lost or laptop sleep). Resume to continue.",
    )
    logger.warning(
        "Pipeline checkpoint: %s cleared stale in-progress %s (age=%.0fs)",
        rfp_id,
        cp.in_progress_phase,
        age,
    )
    return True


async def heal_false_interrupted_checkpoint(
    rfp_id: str,
    *,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> bool:
    """Undo a stale 'connection lost' failure when the manuscript is still updating."""
    if research is None:
        research = await aget_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return False
    cp = research.pipeline_checkpoint
    if cp.in_progress_phase or not cp.last_failed_phase:
        return False
    err = (cp.last_error or "").lower()
    if "interrupted" not in err and "connection lost" not in err:
        return False
    from app.services.proposal_repository import aget_proposal_draft

    if draft is None:
        draft = await aget_proposal_draft(rfp_id)
    if not _draft_recently_saved(draft, within_sec=600):
        return False
    phase = cp.last_failed_phase
    if phase not in PIPELINE_PHASES:
        return False
    healed = cp.model_copy(
        update={
            "in_progress_phase": phase,
            "last_failed_phase": None,
            "last_error": None,
            "resume_from_phase": phase,
            "activity_label": cp.activity_label or PHASE_LABELS.get(phase, phase),
            "updated_at": _now_iso(),
        }
    )
    await _save_checkpoint(rfp_id, healed)
    logger.info(
        "Pipeline checkpoint: %s healed false interrupt — restored in-progress %s",
        rfp_id,
        phase,
    )
    return True


async def record_phase_started(rfp_id: str, phase: str) -> None:
    await clear_stale_in_progress_checkpoint(rfp_id)
    research = await _ensure_research(rfp_id)
    prior = research.pipeline_checkpoint
    phase_label = PHASE_LABELS.get(phase, phase)
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=prior.last_completed_phase if prior else None,
        inProgressPhase=phase,
        lastFailedPhase=None,
        lastError=None,
        resumeFromPhase=phase,
        activityLabel=phase_label,
        activityDetail=None,
        stepIndex=None,
        stepTotal=None,
        updatedAt=_now_iso(),
    )
    await _save_checkpoint(rfp_id, checkpoint)
    logger.info("Pipeline checkpoint: %s started %s", rfp_id, phase)


async def record_pipeline_activity(
    rfp_id: str,
    *,
    label: str,
    detail: str | None = None,
    step_index: int | None = None,
    step_total: int | None = None,
    in_progress_phase: str | None = None,
) -> None:
    """Update live sub-step text while a phase runs (polled by the UI)."""
    research = await _ensure_research(rfp_id)
    cp = research.pipeline_checkpoint
    if cp is None:
        cp = ProposalPipelineCheckpoint(
            inProgressPhase=in_progress_phase or "phase-3",
            activityLabel=label[:500],
            activityDetail=detail[:500] if detail else None,
            stepIndex=step_index,
            stepTotal=step_total,
            updatedAt=_now_iso(),
        )
    else:
        updates: dict[str, object] = {
            "activity_label": label[:500],
            "activity_detail": detail[:500] if detail else None,
            "step_index": step_index,
            "step_total": step_total,
            "updated_at": _now_iso(),
        }
        if in_progress_phase is not None:
            updates["in_progress_phase"] = in_progress_phase
        cp = cp.model_copy(update=updates)
    await _save_checkpoint(rfp_id, cp)


async def clear_fulfill_scan_activity(rfp_id: str) -> None:
    """Clear transient Scan RFP progress without touching a real pipeline phase."""
    research = await aget_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return
    cp = research.pipeline_checkpoint
    if cp.in_progress_phase != "fulfill-scan":
        return
    await _save_checkpoint(
        rfp_id,
        cp.model_copy(
            update={
                "in_progress_phase": None,
                "activity_label": None,
                "activity_detail": None,
                "step_index": None,
                "step_total": None,
                "updated_at": _now_iso(),
            }
        ),
    )


def _next_phase_after(completed_phase: str) -> str:
    idx = _phase_index(completed_phase)
    if idx + 1 >= len(PIPELINE_PHASES):
        return "complete"
    return PIPELINE_PHASES[idx + 1]


async def record_phase_completed(rfp_id: str, phase: str) -> None:
    if phase == "sections-1-3":
        from app.services.proposal_repository import aget_proposal_draft

        draft = await aget_proposal_draft(rfp_id)
        if not static_sections_1_3_have_content(draft):
            await record_phase_failed(
                rfp_id,
                phase,
                "Sections 1–3 incomplete — one or more static sections (Company, Team, Case Studies) has no content",
            )
            return

    if phase == "phase-3-6-self-edit":
        from app.services.proposal_repository import aget_proposal_draft

        draft = await aget_proposal_draft(rfp_id)
        if draft:
            remaining = count_verify_tags(draft)
            if remaining > 0:
                logger.info(
                    "Pipeline checkpoint: %s self-edit done with %d VERIFY tag(s) — manual handoff, not blocking",
                    rfp_id,
                    remaining,
                )

    next_phase = _next_phase_after(phase)
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=phase,
        inProgressPhase=None,
        lastFailedPhase=None,
        lastError=None,
        resumeFromPhase=None if next_phase == "complete" else next_phase,
        activityLabel=None,
        activityDetail=None,
        stepIndex=None,
        stepTotal=None,
        updatedAt=_now_iso(),
    )
    await _save_checkpoint(rfp_id, checkpoint)
    logger.info("Pipeline checkpoint: %s completed %s (next=%s)", rfp_id, phase, next_phase)


async def record_phase_failed(rfp_id: str, phase: str, error: str) -> None:
    research = await aget_research_cache(rfp_id)
    prior = research.pipeline_checkpoint if research else None
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=prior.last_completed_phase if prior else None,
        inProgressPhase=None,
        lastFailedPhase=phase,
        lastError=error[:2000] if error else None,
        resumeFromPhase=phase,
        updatedAt=_now_iso(),
    )
    await _save_checkpoint(rfp_id, checkpoint)
    logger.warning("Pipeline checkpoint: %s failed at %s — %s", rfp_id, phase, error[:200])


async def clear_pipeline_checkpoint(rfp_id: str) -> None:
    research = await aget_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return
    updated = research.model_copy(
        update={"pipeline_checkpoint": None, "updated_at": _now_iso()}
    )
    await asave_research_cache(updated)


async def record_generation_stopped(rfp_id: str, phase: str | None = None) -> None:
    """User hit Stop — clear in-progress, keep completed work, set resume pointer."""
    research = await aget_research_cache(rfp_id)
    prior = research.pipeline_checkpoint if research else None
    active = phase or (prior.in_progress_phase if prior else None) or "phase-3"
    resume: str | None = None
    if active in PIPELINE_PHASES:
        resume = active
    elif prior and prior.resume_from_phase and prior.resume_from_phase in PIPELINE_PHASES:
        resume = prior.resume_from_phase
    elif prior and prior.last_completed_phase and prior.last_completed_phase in PIPELINE_PHASES:
        resume = _next_phase_after(prior.last_completed_phase)
        if resume == "complete":
            resume = prior.last_completed_phase
    failed = active if active in PIPELINE_PHASES else (prior.last_failed_phase if prior else None)
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=prior.last_completed_phase if prior else None,
        inProgressPhase=None,
        lastFailedPhase=failed,
        lastError="Stopped by user. Progress is saved — use Continue proposal to resume.",
        resumeFromPhase=resume,
        activityLabel=None,
        activityDetail=None,
        stepIndex=None,
        stepTotal=None,
        updatedAt=_now_iso(),
    )
    await _save_checkpoint(rfp_id, checkpoint)
    logger.info("Pipeline checkpoint: %s stopped during %s (resume=%s)", rfp_id, active, resume)


@asynccontextmanager
async def pipeline_phase(rfp_id: str, phase: str):
    from app.services.proposal_generation_cancel import (
        ProposalGenerationCancelled,
        bind_active_rfp,
        check_generation_cancelled,
        unbind_active_rfp,
    )

    token = bind_active_rfp(rfp_id)
    await record_phase_started(rfp_id, phase)
    try:
        await check_generation_cancelled(rfp_id)
        yield
        await check_generation_cancelled(rfp_id)
        await record_phase_completed(rfp_id, phase)
    except ProposalGenerationCancelled as exc:
        await record_generation_stopped(rfp_id, phase)
        raise exc
    except Exception as exc:
        await record_phase_failed(rfp_id, phase, str(exc))
        raise
    finally:
        unbind_active_rfp(token)


def phase_is_complete(
    *,
    draft: ProposalDraft | None,
    research: ProposalResearchCache | None,
    phase: str,
) -> bool:
    if phase == "sections-1-3":
        return static_sections_1_3_have_content(draft)

    if not research:
        return False

    if phase == "phase-2":
        plan = research.proposal_execution_plan
        if plan is not None:
            if hasattr(plan, "validation"):
                return plan.validation.readiness_status == "ready" and bool(
                    research.rfp_sections
                )
            if isinstance(plan, dict):
                status = (plan.get("validation") or {}).get("readinessStatus")
                return status == "ready" and bool(research.rfp_sections)
        # Legacy caches created before intelligence layer
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
        if _self_edit_considered_complete(draft=draft, research=research):
            return True
        cp = research.pipeline_checkpoint
        if cp and cp.last_failed_phase == phase:
            err = (cp.last_error or "").lower()
            if "verify" in err and phase_is_complete(draft=draft, research=research, phase="phase-3"):
                return True
            return False
        return False

    if phase == "phase-3-5-budget":
        return research.budget is not None

    if phase == "phase-4-review":
        return research.presubmit_review is not None

    return False


def _has_resumable_pipeline_progress(
    draft: ProposalDraft | None,
    research: ProposalResearchCache | None,
) -> bool:
    """True only when there is real progress to continue — not an empty post-Reset shell."""
    if research is not None:
        cp = research.pipeline_checkpoint
        if cp and (
            cp.last_completed_phase or cp.last_failed_phase or cp.in_progress_phase
        ):
            return True
        if research.rfp_sections or research.evidence_corpus:
            return True
        if research.budget is not None or research.presubmit_review is not None:
            return True
    if draft is not None:
        for section in draft.sections:
            if section.content and section.content.strip():
                return True
    return False


async def resolve_resume_phase(
    rfp_id: str,
    *,
    draft: ProposalDraft | None,
    research: ProposalResearchCache | None,
) -> str:
    from app.services.proposal_repository import aget_proposal_draft

    if draft is None:
        draft = await aget_proposal_draft(rfp_id)
    if research is None:
        research = await aget_research_cache(rfp_id)

    if draft is not None and not static_sections_1_3_have_content(draft):
        return "sections-1-3"

    cp = research.pipeline_checkpoint if research else None
    if cp:
        if cp.last_failed_phase and cp.last_failed_phase in PIPELINE_PHASES:
            if cp.last_failed_phase == "phase-3-6-self-edit":
                err = (cp.last_error or "").lower()
                if ("verify" in err or "placeholder" in err) and not phase_is_complete(
                    draft=draft, research=research, phase="phase-3-5-budget"
                ):
                    return "phase-3-5-budget"
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
        if not research.presubmit_review:
            return "phase-4-review"
        if not research.proof_points:
            return "phase-2"
    return "complete"


async def build_pipeline_status(
    rfp_id: str,
    *,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> dict[str, object]:
    from app.services.proposal_repository import aget_proposal_draft

    if draft is None:
        draft = await aget_proposal_draft(rfp_id)
    if research is None:
        research = await aget_research_cache(rfp_id)

    await clear_stale_in_progress_checkpoint(rfp_id, research=research, draft=draft)
    await heal_false_interrupted_checkpoint(rfp_id, draft=draft, research=research)
    if research is None:
        research = await aget_research_cache(rfp_id)

    resume_from = await resolve_resume_phase(rfp_id, draft=draft, research=research)
    completed = [
        phase
        for phase in PIPELINE_PHASES
        if phase_is_complete(draft=draft, research=research, phase=phase)
    ]
    cp = research.pipeline_checkpoint if research else None
    has_progress = _has_resumable_pipeline_progress(draft, research)
    return {
        "resumeFromPhase": resume_from,
        "completedPhases": completed,
        "isComplete": resume_from == "complete",
        # Empty default outline after Reset is NOT resumable — that is a fresh Generate.
        "canResume": has_progress
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
