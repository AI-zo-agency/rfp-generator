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
    "phase-3-5-budget",
    "phase-3-6-self-edit",
    "phase-4-review",
)

PHASE_LABELS: dict[str, str] = {
    "sections-1-3": "Sections 1–3",
    "phase-2": "Phase 2 intelligence",
    "phase-3": "Phase 3 drafting",
    "phase-3-6-self-edit": "Senior editor polish",
    "phase-3-5-budget": "Budget build",
    "phase-4-review": "Pre-submit review",
    "fulfill-scan": "Complete & clean draft",
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
    # Self-edit: seed step 1 immediately so the UI doesn't flash wrong substep.
    if phase == "phase-3-6-self-edit":
        activity_label = "Senior editor: Removing duplicates"
        activity_detail = "Scanning sections for repeated content…"
        step_index: int | None = 1
        step_total: int | None = 3
    else:
        activity_label = phase_label
        activity_detail = None
        step_index = None
        step_total = None
    checkpoint = ProposalPipelineCheckpoint(
        lastCompletedPhase=prior.last_completed_phase if prior else None,
        inProgressPhase=phase,
        lastFailedPhase=None,
        lastError=None,
        resumeFromPhase=phase,
        activityLabel=activity_label,
        activityDetail=activity_detail,
        stepIndex=step_index,
        stepTotal=step_total,
        resumeFulfillStep=(
            prior.resume_fulfill_step
            if phase == "fulfill-scan" and prior
            else None
        ),
        lastCompletedFulfillStep=(
            prior.last_completed_fulfill_step
            if phase == "fulfill-scan" and prior
            else None
        ),
        updatedAt=_now_iso(),
    )
    if (
        phase == "fulfill-scan"
        and prior
        and (checkpoint.resume_fulfill_step or 0) > 1
    ):
        # Keep the UI on the saved step instead of flashing 1/19 while skips run.
        checkpoint = checkpoint.model_copy(
            update={
                "step_index": checkpoint.resume_fulfill_step,
                "step_total": prior.step_total or 19,
                "activity_label": prior.activity_label or activity_label,
                "activity_detail": "Resuming Complete & clean from the saved step.",
            }
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
    resume_step = step_index if (in_progress_phase or (cp.in_progress_phase if cp else None)) == "fulfill-scan" else None
    last_done = (step_index - 1) if resume_step and step_index and step_index > 1 else None
    if cp is None:
        cp = ProposalPipelineCheckpoint(
            inProgressPhase=in_progress_phase or "phase-3",
            activityLabel=label[:500],
            activityDetail=detail[:500] if detail else None,
            stepIndex=step_index,
            stepTotal=step_total,
            resumeFulfillStep=resume_step,
            lastCompletedFulfillStep=last_done,
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
        if resume_step:
            updates["resume_fulfill_step"] = resume_step
            if last_done:
                updates["last_completed_fulfill_step"] = last_done
        cp = cp.model_copy(update=updates)
    await _save_checkpoint(rfp_id, cp)


def fulfill_resume_step(research: ProposalResearchCache | None) -> int:
    """Step to resume Complete & clean from (1 = start from the beginning)."""
    if not research or not research.pipeline_checkpoint:
        return 1
    cp = research.pipeline_checkpoint
    for raw in (
        cp.resume_fulfill_step,
        cp.step_index if cp.in_progress_phase == "fulfill-scan" else None,
        (cp.last_completed_fulfill_step + 1) if cp.last_completed_fulfill_step else None,
    ):
        if raw is None:
            continue
        try:
            step = int(raw)
        except (TypeError, ValueError):
            continue
        if step >= 1:
            return step
    return 1


async def complete_fulfill_scan(rfp_id: str, *, scan_hash: str | None = None) -> None:
    """Scan finished — drop resume pointer so the next run starts fresh.

    ``scan_hash`` (when given) is the fingerprint of the draft + RFP text this
    run just finished checking, saved so the next *fresh* (non-resume) run can
    tell there is nothing new to check — see ``fulfill_scan_is_already_clean``.
    """
    research = await aget_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return
    cp = research.pipeline_checkpoint
    updates: dict[str, object] = {
        "in_progress_phase": (
            None if cp.in_progress_phase == "fulfill-scan" else cp.in_progress_phase
        ),
        "activity_label": None,
        "activity_detail": None,
        "step_index": None,
        "step_total": None,
        "resume_fulfill_step": None,
        "last_completed_fulfill_step": None,
        "updated_at": _now_iso(),
    }
    if scan_hash:
        updates["last_clean_fulfill_scan_hash"] = scan_hash
        # UI "already clean" signal — survives refresh / reaches other users
        # because it is persisted here by the Celery task, not in a browser.
        updates["last_clean_fulfill_scan_at"] = _now_iso()
    await _save_checkpoint(rfp_id, cp.model_copy(update=updates))


def compute_fulfill_scan_hash(draft: ProposalDraft, rfp_text: str = "") -> str:
    """Fingerprint of the DRAFT CONTENT a Complete & clean run inspects.

    Content-based, not timestamp-based: a section id/content change flips it,
    but a bare re-save that does not change any content leaves it identical —
    so the "already clean" signal survives incidental saves/interactions. The
    ``rfp_text`` arg is accepted for backwards compatibility but intentionally
    NOT hashed: RFP text is stable after generation, and including it made
    the hash impossible to recompute in the status endpoint (no rfp_text there).
    """
    import hashlib

    del rfp_text
    parts = [f"{s.id}\x00{s.content or ''}" for s in draft.sections]
    blob = "\x01".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(blob).hexdigest()


def fulfill_scan_is_already_clean(
    *,
    research: ProposalResearchCache | None,
    resume_at: int,
    current_hash: str,
) -> bool:
    """True only for a fresh (non-resuming) run whose content exactly matches
    the last fully-completed Complete & clean draft run — nothing changed, so
    every one of the 18 steps would find the same (already-fixed) state again.

    Never true when resuming a stopped/interrupted run (``resume_at`` > 1) —
    that path has its own well-tested step-skip logic and must not be touched
    here; this only short-circuits a brand-new invocation.
    """
    if resume_at > 1:
        return False
    if not research or not research.pipeline_checkpoint:
        return False
    saved = research.pipeline_checkpoint.last_clean_fulfill_scan_hash
    return bool(saved) and saved == current_hash


async def clear_fulfill_scan_activity(rfp_id: str) -> None:
    """Clear live Scan RFP spinner without dropping a stop/resume pointer."""
    research = await aget_research_cache(rfp_id)
    if not research or not research.pipeline_checkpoint:
        return
    cp = research.pipeline_checkpoint
    if cp.in_progress_phase != "fulfill-scan":
        return
    if cp.resume_fulfill_step:
        await _save_checkpoint(
            rfp_id,
            cp.model_copy(
                update={
                    "in_progress_phase": None,
                    "updated_at": _now_iso(),
                }
            ),
        )
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
    if phase == "fulfill-scan":
        # Not a Generate pipeline phase — must not stamp lastCompletedPhase or
        # resumeFromPhase would jump back to Sections 1–3.
        await complete_fulfill_scan(rfp_id)
        return

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
    if active == "fulfill-scan" or (
        prior is not None
        and active not in PIPELINE_PHASES
        and (
            prior.in_progress_phase == "fulfill-scan"
            or (prior.resume_fulfill_step or 0) >= 1
            or (prior.last_completed_fulfill_step or 0) >= 1
        )
    ):
        resume_step = None
        if prior:
            resume_step = prior.step_index or prior.resume_fulfill_step
            if resume_step is None and prior.last_completed_fulfill_step:
                resume_step = prior.last_completed_fulfill_step + 1
        checkpoint = ProposalPipelineCheckpoint(
            lastCompletedPhase=prior.last_completed_phase if prior else None,
            inProgressPhase=None,
            lastFailedPhase=prior.last_failed_phase if prior else None,
            lastError=(
                "Stopped by user. Progress is saved — use Complete & clean draft "
                "to resume from the last step."
            ),
            resumeFromPhase=prior.resume_from_phase if prior else None,
            activityLabel=prior.activity_label if prior else None,
            activityDetail=prior.activity_detail if prior else None,
            stepIndex=prior.step_index if prior else None,
            stepTotal=prior.step_total if prior else None,
            lastCompletedFulfillStep=prior.last_completed_fulfill_step if prior else None,
            resumeFulfillStep=resume_step,
            updatedAt=_now_iso(),
        )
        await _save_checkpoint(rfp_id, checkpoint)
        logger.info(
            "Pipeline checkpoint: %s stopped during fulfill-scan (resume_step=%s)",
            rfp_id,
            resume_step,
        )
        return
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
        lastCompletedFulfillStep=prior.last_completed_fulfill_step if prior else None,
        resumeFulfillStep=prior.resume_fulfill_step if prior else None,
        updatedAt=_now_iso(),
    )
    await _save_checkpoint(rfp_id, checkpoint)
    logger.info("Pipeline checkpoint: %s stopped during %s (resume=%s)", rfp_id, active, resume)


@asynccontextmanager
async def pipeline_phase(rfp_id: str, phase: str):
    from app.services.llm_call_context import get_llm_run_id, llm_call_context
    from app.services.proposal_generation_cancel import (
        ProposalGenerationCancelled,
        bind_active_rfp,
        check_generation_cancelled,
        unbind_active_rfp,
    )

    token = bind_active_rfp(rfp_id)
    await record_phase_started(rfp_id, phase)
    run_id = get_llm_run_id() or ""
    try:
        with llm_call_context(rfp_id=rfp_id, run_id=run_id, node_name=phase):
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


def _checkpoint_reached_phase(
    research: ProposalResearchCache | None,
    phase: str,
) -> bool:
    """True when checkpoint lastCompletedPhase is at or past ``phase``.

    Artifact presence alone is not enough — otherwise a stale budget/review from
    an earlier run marks later phases complete and Continue never re-runs them.
    """
    if research is None or research.pipeline_checkpoint is None:
        return False
    last = research.pipeline_checkpoint.last_completed_phase
    if not last:
        return False
    if last == "complete":
        return True
    if last not in PIPELINE_PHASES or phase not in PIPELINE_PHASES:
        return False
    return _phase_index(last) >= _phase_index(phase)


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
        from app.services.proposal_draft_llm import SECTION_DRAFT_FAILURE_PLACEHOLDER
        from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

        draftable_ids = {
            section.id
            for section in research.rfp_sections
            if not is_duplicate_static_rfp_section(section.title)
        }
        if not draftable_ids:
            return False
        filled = 0
        for section in draft.sections:
            if section.id not in draftable_ids:
                continue
            text = section.content.strip()
            if not text or text == SECTION_DRAFT_FAILURE_PLACEHOLDER.strip():
                continue
            filled += 1
        return filled >= len(draftable_ids)

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
        if research.budget is None:
            return False
        # Hard failure (e.g. grounding contradictions) must not count as complete
        # merely because a partial budget artifact was persisted before the error.
        cp = research.pipeline_checkpoint
        if cp and cp.last_failed_phase == "phase-3-5-budget":
            err = (cp.last_error or "").casefold()
            if any(
                marker in err
                for marker in (
                    "grounding",
                    "pricing contradiction",
                    "unresolved pricing",
                    "rate card unusable",
                )
            ):
                return False
        # Stale budget artifacts must not skip Phase 3.5 after a newer draft run.
        return _checkpoint_reached_phase(research, "phase-3-5-budget")

    if phase == "phase-4-review":
        if research.presubmit_review is None:
            return False
        # Stale pre-submit reviews (e.g. from a prior day) must not mark the
        # pipeline complete while checkpoint still points at phase-4.
        return _checkpoint_reached_phase(research, "phase-4-review")

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
                # Budget runs before senior editor — if editor failed on VERIFY but budget
                # is missing, finish budget first then return to editor.
                if ("verify" in err or "placeholder" in err) and not phase_is_complete(
                    draft=draft, research=research, phase="phase-3-5-budget"
                ):
                    return "phase-3-5-budget"
            return cp.last_failed_phase
        if cp.in_progress_phase and cp.in_progress_phase in PIPELINE_PHASES:
            return cp.in_progress_phase

    # Always resume at the first incomplete phase. Do not trust resumeFromPhase alone —
    # it can point at phase-4 while a stale budget/review artifact left earlier work skipped.
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
    # "Already clean" = a Complete & clean run finished AND the draft CONTENT is
    # unchanged since (content hash match, not a fragile updated_at timestamp —
    # incidental re-saves must not flip this). Server-derived, so it survives
    # refresh and is the same for every user.
    fulfill_up_to_date = False
    if cp is not None and cp.last_clean_fulfill_scan_hash and draft is not None:
        fulfill_up_to_date = (
            cp.last_clean_fulfill_scan_hash == compute_fulfill_scan_hash(draft)
        )
    return {
        "resumeFromPhase": resume_from,
        "fulfillScanUpToDate": fulfill_up_to_date,
        "fulfillScanCompletedAt": cp.last_clean_fulfill_scan_at if cp else None,
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
