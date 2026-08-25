from collections.abc import Awaitable, Callable
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
import httpx
import logging
import re
from datetime import datetime
from urllib.parse import quote

from app.models.proposal import (
    ProposalAgentActivity,
    ProposalDraft,
    ProposalGoogleDocExportResponse,
    ProposalGenerateResponse,
    ProposalPhase4AutoFixResponse,
    ProposalPhase4Response,
    ProposalPricingResponse,
    ProposalResearchCache,
    ProposalRestoreSnapshotRequest,
    ProposalRestoreSnapshotResponse,
    ProposalSection,
    ProposalSectionImproveResponse,
    ProposalSuggestedFix,
    PreSubmitAutoFixRequest,
    SectionImproveRequest,
)
from app.services.proposal_api_slim import (
    merge_snapshots_for_save,
    slim_draft_for_api,
    slim_research_for_api,
)
from app.services.proposal_pipeline_checkpoint import (
    build_pipeline_status,
    clear_pipeline_checkpoint,
    pipeline_phase,
)
from app.services.proposal_generator import (
    ProposalError,
    generate_full_proposal,
    generate_sections_1_3,
    run_phase2_retrieval,
    run_phase3_5_budget,
    run_phase3_5_budget_reconcile,
    run_phase3_6_self_edit,
    run_phase3_drafting,
    run_phase4_presubmit_autofix,
    run_phase4_presubmit_review,
    run_phase4_finalize_gaps,
)
from app.services.proposal_section_editor import improve_proposal_section
from app.services.proposal_repository import (
    get_proposal_draft,
    get_research_cache,
    save_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    adelete_proposal_draft,
    adelete_research_cache,
)
from app.services.proposal_job_runner import (
    get_proposal_job,
    proposal_job_to_dict,
    start_proposal_job,
)
from app.services.rfp_repository import get_rfp, rfp_exists

router = APIRouter(prefix="/rfps", tags=["proposals"])
logger = logging.getLogger(__name__)


async def _enqueue_pipeline_phase(
    rfp_id: str,
    phase: str,
    work: Callable[[], Awaitable[Any]],
    *,
    timeout_sec: float | None = None,
    job_kwargs: dict[str, Any] | None = None,
) -> JSONResponse:
    """Start a long phase and return immediately (202).

    Clients poll GET /proposal (checkpoint + draft) until the phase completes.
    Does not cancel on HTTP disconnect — Stop is explicit via POST /stop.

    In production (settings.celery_enabled — REDIS_URL set), the phase runs
    on a separate Celery worker, dispatched via app.celery_app.
    run_pipeline_phase_task with `job_kwargs` as its JSON-serializable
    arguments (a plain Python closure like `work` can't cross the process
    boundary to a worker). Locally without Redis, `work` runs in-process via
    asyncio.create_task exactly as before Celery existed.

    timeout_sec is a last-resort safety net for the in-process path only —
    Celery's own task_time_limit (app/celery_app.py) covers the worker path.
    If the phase is still running past this ceiling, it is cancelled and
    recorded as a failed/resumable checkpoint instead of running forever and
    holding the per-rfp job lock (the 409 other-operations-blocked behavior)
    indefinitely.
    """
    existing = await get_proposal_job(rfp_id)
    if existing and existing.status == "running":
        if existing.job_type == phase:
            return JSONResponse(
                status_code=202,
                content={
                    "ok": True,
                    "started": False,
                    "alreadyRunning": True,
                    "phase": phase,
                    "job": proposal_job_to_dict(existing),
                },
            )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Another proposal job is already running ({existing.job_type}). "
                "Stop it or wait before starting a different phase."
            ),
        )

    from app.services.proposal_generation_cancel import clear_generation_cancel
    from app.services.proposal_pipeline_checkpoint import record_phase_started

    # A prior Stop leaves an in-memory cancel flag. Starting a new job is an
    # explicit "run again" — drop the stale flag or the first cancelled-check
    # kills the work immediately (looks like the scan "just stopped").
    clear_generation_cancel(rfp_id)

    # Mark in-progress before returning so the first poll sees the phase.
    await record_phase_started(rfp_id, phase)

    import uuid

    from app.services.llm_call_context import llm_call_context

    run_id = str(uuid.uuid4())

    async def _run() -> Any:
        import asyncio

        try:
            async with pipeline_phase(rfp_id, phase):
                with llm_call_context(rfp_id=rfp_id, run_id=run_id, node_name=phase):
                    if timeout_sec is not None:
                        return await asyncio.wait_for(work(), timeout=timeout_sec)
                    return await work()
        except TimeoutError:
            # pipeline_phase's own except-Exception cleanup does not run here:
            # wait_for cancels work() by raising CancelledError inside the
            # "async with" block, which is a BaseException the phase context
            # manager does not catch — so the checkpoint is repaired explicitly
            # here instead, the same way POST /stop does it.
            from app.services.proposal_pipeline_checkpoint import record_phase_failed

            await record_phase_failed(
                rfp_id,
                phase,
                f"Timed out after {int(timeout_sec or 0)}s without completing. "
                "Progress up to the last saved step is kept — run it again to resume.",
            )
            raise

    def _celery_dispatch() -> Any:
        from app.celery_app import run_pipeline_phase_task

        return run_pipeline_phase_task.delay(rfp_id, phase, job_kwargs or {})

    record = await start_proposal_job(
        rfp_id, phase, _run, celery_dispatch=_celery_dispatch
    )
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "started": True,
            "alreadyRunning": False,
            "phase": phase,
            "job": proposal_job_to_dict(record),
        },
    )


def _slim_research(research: ProposalResearchCache | None) -> ProposalResearchCache | None:
    if not research:
        return None
    return slim_research_for_api(research)


def _parse_draft_updated_at(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("/{rfp_id}/proposal")
async def get_proposal(rfp_id: str) -> dict[str, object]:
    import asyncio

    from app.services.proposal_repository import aget_proposal_draft, aget_research_cache

    draft = None
    research = None
    last_exc: httpx.HTTPError | None = None
    for attempt in range(3):
        try:
            draft, research = await asyncio.gather(
                aget_proposal_draft(rfp_id),
                aget_research_cache(rfp_id),
            )
            last_exc = None
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= 2:
                raise HTTPException(
                    status_code=503,
                    detail="Temporary data-store connection issue. Please retry.",
                ) from exc
            await asyncio.sleep(0.4 * (attempt + 1))
    if last_exc is not None:
        raise HTTPException(
            status_code=503,
            detail="Temporary data-store connection issue. Please retry.",
        ) from last_exc
    if draft is None and research is None and not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    if draft is not None:
        from app.services.evidence_trust.personnel_grounding import (
            scrub_fabricated_personnel_from_draft,
        )
        from app.services.proposal_draft_snapshots import prune_clutter_snapshots
        from app.services.proposal_repository import asave_proposal_draft

        pruned = prune_clutter_snapshots(draft)
        scrubbed, personnel_logs = scrub_fabricated_personnel_from_draft(pruned)
        from app.services.proposal_closing_hollow_repair import (
            repair_hollow_closing_sections,
        )

        scrubbed, closing_logs = repair_hollow_closing_sections(scrubbed)
        from app.services.proposal_capability_bio_grounding import (
            repair_misplaced_bio_stub_sections,
        )
        from app.services.proposal_scan_fact_repairs import fill_hollow_project_team_from_bios

        scrubbed, bio_stub_logs = repair_misplaced_bio_stub_sections(scrubbed)
        # Cheap sync fallback only — LLM won-proposal fill runs on Generate + Scan.
        scrubbed, team_logs = fill_hollow_project_team_from_bios(scrubbed)
        snapshots_changed = [s.saved_at for s in (pruned.snapshots or [])] != [
            s.saved_at for s in (draft.snapshots or [])
        ]
        heal_logs = [*personnel_logs, *closing_logs, *bio_stub_logs, *team_logs]
        if snapshots_changed or heal_logs:
            await asave_proposal_draft(scrubbed)
            draft = scrubbed
            if heal_logs:
                logger.info(
                    "Draft heal on load for %s: %s",
                    rfp_id,
                    "; ".join(heal_logs[:6]),
                )
        else:
            draft = pruned
    job = await get_proposal_job(rfp_id)
    slim_research = _slim_research(research)
    pipeline_status = await build_pipeline_status(rfp_id, draft=draft, research=research)
    return {
        "draft": slim_draft_for_api(draft) if draft else None,
        "research": slim_research.model_dump(by_alias=True) if slim_research else None,
        "pipelineStatus": pipeline_status,
        "proposalJob": proposal_job_to_dict(job),
    }


@router.get("/{rfp_id}/proposal/snapshot")
async def get_proposal_snapshot_query(
    rfp_id: str,
    saved_at: str = Query(..., alias="savedAt"),
) -> dict[str, object]:
    """Full snapshot sections for version compare (query param — avoids '+' path mangling)."""
    return await _get_proposal_snapshot(rfp_id, saved_at)


@router.get("/{rfp_id}/proposal/snapshot/{saved_at:path}")
async def get_proposal_snapshot_path(rfp_id: str, saved_at: str) -> dict[str, object]:
    """Legacy path form — prefer ?savedAt= for ISO timestamps with offsets."""
    return await _get_proposal_snapshot(rfp_id, saved_at)


def _normalize_snapshot_saved_at(value: str) -> str:
    from app.services.proposal_draft_snapshots import normalize_snapshot_saved_at

    return normalize_snapshot_saved_at(value)


async def _get_proposal_snapshot(rfp_id: str, saved_at: str) -> dict[str, object]:
    from app.services.proposal_repository import aget_proposal_draft

    key = _normalize_snapshot_saved_at(saved_at)
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        raise HTTPException(status_code=404, detail="No proposal draft found.")
    for snap in draft.snapshots or []:
        if _normalize_snapshot_saved_at(snap.saved_at) == key:
            return {"snapshot": snap.model_dump(by_alias=True)}
    raise HTTPException(status_code=404, detail="Snapshot not found.")


@router.get("/{rfp_id}/proposal/job-status")
async def get_proposal_job_status(rfp_id: str) -> dict[str, object]:
    if not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    job = await get_proposal_job(rfp_id)
    return {"job": proposal_job_to_dict(job)}


@router.put("/{rfp_id}/proposal")
def upsert_proposal(rfp_id: str, draft: ProposalDraft) -> dict[str, object]:
    if not get_rfp(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    if draft.rfp_id != rfp_id:
        raise HTTPException(status_code=400, detail="rfpId mismatch")
    existing = get_proposal_draft(rfp_id)
    # Guard: client autosave of an empty shell must not wipe a filled manuscript.
    # Snapshots alone survive that bug — Restore version still works — but live sections
    # would show "Not started" after reload. Explicit Reset deletes the row first.
    if existing is not None:
        existing_filled = sum(
            1 for s in existing.sections if (s.content or "").strip()
        )
        incoming_filled = sum(
            1 for s in draft.sections if (s.content or "").strip()
        )
        if existing_filled > 0 and incoming_filled == 0:
            research = get_research_cache(rfp_id)
            completed = None
            if research and research.pipeline_checkpoint:
                completed = research.pipeline_checkpoint.last_completed_phase
            detail = (
                "Refusing to overwrite a filled proposal with an empty outline. "
                "Use Reset draft if you intend to clear the manuscript."
            )
            if completed:
                detail = (
                    f"Refusing to overwrite a filled proposal (pipeline reached "
                    f"{completed}) with an empty outline. "
                    "Use Reset draft if you intend to clear the manuscript."
                )
            raise HTTPException(status_code=409, detail=detail)

        # Guard: stale autosave must not drop sections the server just added (chat add-bio).
        existing_ids = {s.id for s in existing.sections}
        incoming_ids = {s.id for s in draft.sections}
        dropped = existing_ids - incoming_ids
        if dropped:
            ex_t = _parse_draft_updated_at(existing.updated_at)
            in_t = _parse_draft_updated_at(draft.updated_at)
            if not (ex_t and in_t and in_t > ex_t):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Refusing to drop proposal sections via stale autosave. "
                        "Reload the draft — newer sections are already saved."
                    ),
                )

    # None = unset (preserve). Explicit [] clears. Non-empty keeps.
    if (
        existing
        and existing.selected_key_personas
        and draft.selected_key_personas is None
    ):
        draft.selected_key_personas = existing.selected_key_personas

    draft = merge_snapshots_for_save(draft, existing)
    from app.services.proposal_draft_snapshots import prune_clutter_snapshots

    draft = prune_clutter_snapshots(draft)
    save_proposal_draft(draft)
    return {"ok": True, "draft": slim_draft_for_api(draft)}


@router.get("/{rfp_id}/proposal/archives")
async def list_proposal_archives_endpoint(rfp_id: str) -> dict[str, object]:
    if not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    from app.services.proposal_draft_archives import archive_meta_dict
    from app.services.proposal_repository import alist_proposal_draft_archives

    rows = await alist_proposal_draft_archives(rfp_id)
    return {"archives": [archive_meta_dict(row) for row in rows]}


@router.post("/{rfp_id}/proposal/archives/{archive_id}/restore")
async def restore_proposal_archive_endpoint(
    rfp_id: str, archive_id: str
) -> dict[str, object]:
    if not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    from app.services.proposal_draft_archives import (
        REASON_BEFORE_ARCHIVE_RESTORE,
        archive_filled_draft,
    )
    from app.services.proposal_repository import (
        aget_proposal_draft,
        aget_proposal_draft_archive,
        arestore_proposal_draft_archive,
    )

    archived = await aget_proposal_draft_archive(rfp_id, archive_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Archive not found.")
    current = await aget_proposal_draft(rfp_id)
    await archive_filled_draft(
        current,
        reason=REASON_BEFORE_ARCHIVE_RESTORE,
        label="Before archive restore",
    )
    try:
        draft = await arestore_proposal_draft_archive(rfp_id, archive_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "draft": slim_draft_for_api(draft)}


@router.post("/{rfp_id}/proposal/reset")
async def reset_proposal_endpoint(rfp_id: str) -> dict[str, object]:
    """Hard-reset: archive filled draft, then wipe draft + checkpoint so generation starts fresh."""
    if not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    from app.services.proposal_draft_archives import (
        REASON_BEFORE_RESET,
        archive_filled_draft,
    )
    from app.services.proposal_repository import aget_proposal_draft

    try:
        current = await aget_proposal_draft(rfp_id)
        await archive_filled_draft(
            current,
            reason=REASON_BEFORE_RESET,
            label="Before Reset draft",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failed to archive draft before reset for %s", rfp_id
        )
    try:
        await adelete_proposal_draft(rfp_id)
    except Exception:
        pass
    try:
        await adelete_research_cache(rfp_id)
    except Exception:
        pass
    await clear_pipeline_checkpoint(rfp_id)
    from app.services.proposal_generation_cancel import clear_generation_cancel

    clear_generation_cancel(rfp_id)
    return {
        "ok": True,
        "message": (
            "Proposal draft and all checkpoints cleared from database. "
            "A filled manuscript was archived first when one existed."
        ),
    }


@router.post("/{rfp_id}/proposal/restart-from-intelligence")
async def restart_from_intelligence_endpoint(rfp_id: str) -> dict[str, object]:
    """Keep Sections 1–3; delete Intelligence / RFP tabs / budget / review so Phase 2 rebuilds clean."""
    if not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")

    from datetime import datetime, timezone

    from app.services.proposal_draft_archives import (
        REASON_BEFORE_RESET,
        archive_filled_draft,
    )
    from app.services.proposal_generator import _static_sections_from_draft
    from app.services.proposal_generation_cancel import clear_generation_cancel
    from app.services.proposal_repository import (
        aget_proposal_draft,
        asave_proposal_draft,
    )
    from app.models.proposal import ProposalDraft

    current = await aget_proposal_draft(rfp_id)
    try:
        await archive_filled_draft(
            current,
            reason=REASON_BEFORE_RESET,
            label="Before Start from Intelligence",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failed to archive draft before restart-from-intelligence for %s", rfp_id
        )

    page_limit = None
    if current and current.sections:
        page_limit = next(
            (s.page_limit for s in current.sections if s.page_limit),
            None,
        )
    static = _static_sections_from_draft(current, page_limit)
    now = datetime.now(timezone.utc).isoformat()
    stripped = ProposalDraft(
        rfpId=rfp_id,
        sections=static,
        updatedAt=now,
        generatedAt=current.generated_at if current else None,
        provider=current.provider if current else None,
        snapshots=current.snapshots if current else [],
        selectedKeyPersonas=(
            list(current.selected_key_personas)
            if current and current.selected_key_personas is not None
            else None
        ),
    )
    await asave_proposal_draft(stripped)

    try:
        await adelete_research_cache(rfp_id)
    except Exception:
        pass
    await clear_pipeline_checkpoint(rfp_id)
    clear_generation_cancel(rfp_id)

    return {
        "ok": True,
        "draft": slim_draft_for_api(stripped),
        "message": (
            "Cleared Intelligence, RFP tabs, budget, and review. "
            "Sections 1–3 kept. Ready to rebuild from Phase 2."
        ),
    }


@router.post("/{rfp_id}/proposal/restart-from-case-studies")
async def restart_from_case_studies_endpoint(rfp_id: str) -> dict[str, object]:
    """Keep Company + Team Bios; strip Our Work + Intelligence so case-study extraction re-runs."""
    if not rfp_exists(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")

    from datetime import datetime, timezone

    from app.services.proposal_draft_archives import (
        REASON_BEFORE_RESET,
        archive_filled_draft,
    )
    from app.services.proposal_generator import _default_sections
    from app.services.proposal_generation_cancel import clear_generation_cancel
    from app.services.proposal_repository import (
        aget_proposal_draft,
        asave_proposal_draft,
    )
    from app.models.proposal import ProposalDraft

    current = await aget_proposal_draft(rfp_id)
    try:
        await archive_filled_draft(
            current,
            reason=REASON_BEFORE_RESET,
            label="Before Start from Case Studies",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failed to archive draft before restart-from-case-studies for %s", rfp_id
        )

    page_limit = None
    if current and current.sections:
        page_limit = next(
            (s.page_limit for s in current.sections if s.page_limit),
            None,
        )

    kept: list = []
    if current and current.sections:
        for section in current.sections:
            sid = section.id
            if sid.startswith("section-1-"):
                kept.append(section)
            elif sid.startswith("section-2-bio-") and sid != "section-2-bio-placeholder":
                kept.append(section)

    # Ensure Section 3 placeholder so the UI/sidebar still shows Our Work while
    # extraction rewrites the cards.
    defaults = _default_sections(page_limit)
    placeholder = next(
        (s for s in defaults if s.id == "section-3-work-placeholder"),
        None,
    )
    if placeholder is not None:
        kept.append(placeholder)

    now = datetime.now(timezone.utc).isoformat()
    stripped = ProposalDraft(
        rfpId=rfp_id,
        sections=kept,
        updatedAt=now,
        generatedAt=current.generated_at if current else None,
        provider=current.provider if current else None,
        snapshots=current.snapshots if current else [],
        selectedKeyPersonas=(
            list(current.selected_key_personas)
            if current and current.selected_key_personas is not None
            else None
        ),
    )
    await asave_proposal_draft(stripped)

    try:
        await adelete_research_cache(rfp_id)
    except Exception:
        pass
    await clear_pipeline_checkpoint(rfp_id)
    clear_generation_cancel(rfp_id)

    return {
        "ok": True,
        "draft": slim_draft_for_api(stripped),
        "message": (
            "Cleared Our Work case studies, Intelligence, RFP tabs, budget, and review. "
            "Company + Team Bios kept. Ready to re-run case-study extraction."
        ),
    }


@router.post("/{rfp_id}/proposal/match-case-studies")
async def match_case_studies_endpoint(rfp_id: str) -> dict[str, object]:
    """Rank KB case studies against this RFP (one-click — no full proposal run)."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    from app.services.proposal_case_study_match import match_case_studies_for_rfp

    try:
        result = await match_case_studies_for_rfp(rfp)
    except Exception as exc:
        logger.exception("match-case-studies failed for %s", rfp_id)
        raise HTTPException(
            status_code=502,
            detail=f"Case study match failed: {exc}",
        ) from exc

    return result.model_dump(by_alias=True)


@router.post("/{rfp_id}/proposal/stop")
async def stop_proposal_generation_endpoint(rfp_id: str) -> dict[str, object]:
    """Request cooperative stop — ends current LLM/Supermemory work and saves checkpoint."""
    from app.services.proposal_generation_cancel import request_generation_cancel
    from app.services.proposal_job_runner import cancel_proposal_job
    from app.services.proposal_pipeline_checkpoint import record_generation_stopped

    request_generation_cancel(rfp_id)
    await cancel_proposal_job(rfp_id)
    research = await aget_research_cache(rfp_id)
    phase = None
    if research and research.pipeline_checkpoint:
        phase = research.pipeline_checkpoint.in_progress_phase
    await record_generation_stopped(rfp_id, phase)
    return {
        "ok": True,
        "message": "Stop requested. Current step will end; use Continue proposal to resume.",
    }


@router.post("/{rfp_id}/proposal/generation/clear-stop")
async def clear_proposal_stop_flag_endpoint(rfp_id: str) -> dict[str, bool]:
    from app.services.proposal_generation_cancel import clear_generation_cancel

    clear_generation_cancel(rfp_id)
    return {"ok": True}


@router.post("/{rfp_id}/proposal/generate", response_model=ProposalGenerateResponse)
async def generate_proposal_endpoint(rfp_id: str) -> ProposalGenerateResponse:
    """Generate full proposal: static Sections 1–3 + RFP-mapped sections from evidence."""
    try:
        draft, brand_voice, research = await generate_full_proposal(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Full proposal generation failed: {exc}",
        ) from exc

    return ProposalGenerateResponse(
        draft=draft,
        brandVoice=brand_voice,
        research=_slim_research(research) or research,
    )


@router.post(
    "/{rfp_id}/proposal/generate/full",
    response_model=ProposalGenerateResponse,
)
async def generate_full_proposal_endpoint(rfp_id: str) -> ProposalGenerateResponse:
    """Same as POST /generate — static Sections 1–3 then RFP-varying sections."""
    try:
        draft, brand_voice, research = await generate_full_proposal(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Full proposal generation failed: {exc}",
        ) from exc

    return ProposalGenerateResponse(
        draft=draft,
        brandVoice=brand_voice,
        research=_slim_research(research) or research,
    )


@router.post(
    "/{rfp_id}/proposal/generate/sections-1-3",
)
async def generate_sections_1_3_endpoint(
    rfp_id: str,
    force_regenerate: bool = Query(
        True,
        description=(
            "true = rebuild all Sections 1–3; false = preserve complete "
            "Company/Bios and only fill missing groups (e.g. Our Work)."
        ),
    ),
) -> JSONResponse:
    """Start static Sections 1–3 in the background; poll GET /proposal for completion.

    force_regenerate=true (default): rebuild all of Sections 1–3.
    force_regenerate=false: keep complete Section 1/2/3 cards; only fill missing
    groups (used by Start from Case Studies after stripping Our Work only).
    """

    async def work() -> None:
        await generate_sections_1_3(rfp_id, force_regenerate=force_regenerate)

    return await _enqueue_pipeline_phase(
        rfp_id,
        "sections-1-3",
        work,
        job_kwargs={"force_regenerate": force_regenerate},
    )


@router.post(
    "/{rfp_id}/proposal/phase-2-retrieval",
)
async def phase2_retrieval_endpoint(rfp_id: str) -> JSONResponse:
    """Start Phase 2 retrieval in the background; poll GET /proposal for completion."""

    async def work() -> None:
        await run_phase2_retrieval(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-2", work)


@router.post(
    "/{rfp_id}/proposal/phase-3-drafting",
)
async def phase3_drafting_endpoint(rfp_id: str) -> JSONResponse:
    """Start Phase 3 drafting in the background; poll GET /proposal for completion."""

    async def work() -> None:
        await run_phase3_drafting(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-3", work)


@router.post(
    "/{rfp_id}/proposal/designer-compact",
)
async def designer_compact_endpoint(rfp_id: str) -> JSONResponse:
    """Compact every overlong tab to designer-ready layout (tables/bullets, full RFP coverage)."""

    async def work() -> None:
        from app.services.proposal_self_edit_loop import run_designer_compact_manuscript

        await run_designer_compact_manuscript(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-3-6-self-edit", work)


@router.post(
    "/{rfp_id}/proposal/phase-3-6-self-edit",
)
async def phase3_6_self_edit_endpoint(rfp_id: str) -> JSONResponse:
    """Start Phase 3.6 self-edit in the background; poll GET /proposal for completion."""

    async def work() -> None:
        await run_phase3_6_self_edit(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-3-6-self-edit", work)


@router.post(
    "/{rfp_id}/proposal/phase-3-5-budget",
)
async def phase3_5_budget_endpoint(rfp_id: str) -> JSONResponse:
    """Start Phase 3.5 budget in the background; poll GET /proposal for completion."""

    async def work() -> None:
        await run_phase3_5_budget(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-3-5-budget", work)


@router.post(
    "/{rfp_id}/proposal/phase-3-5-budget-reconcile",
    response_model=ProposalPricingResponse,
)
async def phase3_5_budget_reconcile_endpoint(rfp_id: str) -> ProposalPricingResponse:
    """Reconcile cached budget line-item math and sync totals through manuscript (no LLM regen)."""
    try:
        draft, research, budget = await run_phase3_5_budget_reconcile(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Budget reconcile failed: {exc}",
        ) from exc

    slim = _slim_research(research) or research
    return ProposalPricingResponse(budget=budget, research=slim, draft=draft)


@router.post(
    "/{rfp_id}/proposal/pricing/generate",
)
async def generate_pricing_endpoint(rfp_id: str) -> JSONResponse:
    """Same as phase-3-5-budget — async start + poll."""

    async def work() -> None:
        await run_phase3_5_budget(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-3-5-budget", work)


def _section_by_id(draft: ProposalDraft | None, section_id: str) -> ProposalSection | None:
    if not draft:
        return None
    return next((s for s in draft.sections if s.id == section_id), None)


async def _salvage_draft_after_improve_failure(
    rfp_id: str,
    *,
    prior_draft: ProposalDraft,
    research: ProposalResearchCache,
) -> tuple[ProposalDraft, bool, list[str]]:
    """If chat/LLM dies, still persist roster + bio designer-note stubs."""
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
    from app.services.proposal_zero_fabrication import (
        apply_zero_fabrication_guards_before_persist,
    )

    rfp_text = ""
    try:
        from app.services.proposal_common import load_rfp_for_proposal

        rfp_text = load_rfp_for_proposal(rfp_id)[2] or ""
    except Exception:  # noqa: BLE001
        rfp_text = ""

    current = await aget_proposal_draft(rfp_id) or prior_draft
    before = tuple((s.id, s.content or "") for s in (prior_draft.sections or []))
    repaired, report = await apply_zero_fabrication_guards_before_persist(
        current,
        research=research,
        budget=research.budget if research else None,
        rfp_text=rfp_text,
        label="chat-failure-salvage",
    )
    after = tuple((s.id, s.content or "") for s in (repaired.sections or []))
    changed = after != before
    if changed:
        await asave_proposal_draft(repaired)
    return repaired, changed, list(report.logs)


def _improve_activity_for_turn(
    *,
    prior_draft: ProposalDraft | None,
    section: ProposalSection,
    draft: ProposalDraft,
    draft_changed: bool,
    assistant_message: str,
    extra_discrepancies: list[str] | None = None,
) -> ProposalAgentActivity:
    from app.services.proposal_chat_activity import build_improve_agent_activity

    prior = _section_by_id(prior_draft, section.id)
    extra_changes: list[str] = []
    if prior_draft:
        before_map = {s.id: s.content or "" for s in prior_draft.sections}
        other = [
            s.title
            for s in draft.sections
            if s.id != section.id
            and s.id in before_map
            and (s.content or "") != before_map[s.id]
        ]
        if other:
            extra_changes.append(
                "Also updated: " + ", ".join(other[:8]) + ("…" if len(other) > 8 else "")
            )
    return build_improve_agent_activity(
        section_title=section.title,
        before=prior.content if prior else "",
        after=section.content or "",
        draft_changed=draft_changed,
        assistant_message=assistant_message,
        extra_changes=extra_changes,
        extra_discrepancies=extra_discrepancies,
    )


@router.post(
    "/{rfp_id}/proposal/sections/{section_id}/improve",
    response_model=ProposalSectionImproveResponse,
)
async def improve_section_endpoint(
    rfp_id: str,
    section_id: str,
    body: SectionImproveRequest,
) -> ProposalSectionImproveResponse:
    """Re-query KB with new detailed queries and re-draft one section from user feedback."""
    from app.services.proposal_repository import aget_proposal_draft, aget_research_cache

    import uuid

    from app.services.llm_call_context import llm_call_context

    prior_draft = await aget_proposal_draft(rfp_id)
    chat_run_id = str(uuid.uuid4())
    try:
        with llm_call_context(rfp_id=rfp_id, run_id=chat_run_id, node_name="section_chat"):
            (
                section,
                draft,
                research,
                _provider,
                assistant_message,
                draft_changed,
                suggested_fix,
            ) = await improve_proposal_section(
                rfp_id,
                section_id,
                body.message,
                selection_start=body.selection_start,
                selection_end=body.selection_end,
                selection_text=body.selection_text,
                conversation_history=[
                    {"role": t.role, "content": t.content} for t in body.conversation_history
                ],
                proposal_wide=body.proposal_wide,
                apply_fix=body.apply_fix,
                improve_section_pinned=body.improve_section_pinned,
            )
    except ProposalError as exc:
        # Policy / rewrite checks must recap in chat — never 422 the UI.
        if exc.status_code in (400, 422) and prior_draft and prior_draft.sections:
            research = await aget_research_cache(rfp_id) or ProposalResearchCache(
                rfpId=rfp_id
            )
            draft, draft_changed, salvage_logs = await _salvage_draft_after_improve_failure(
                rfp_id, prior_draft=prior_draft, research=research
            )
            section = _section_by_id(draft, section_id) or draft.sections[0]
            note = str(exc).strip() or "Could not complete this instruction."
            if draft_changed:
                assistant_message = (
                    f"{note} Applied deterministic roster/bio stubs so invented "
                    "names and resume dumps are not left in the manuscript."
                )
            else:
                assistant_message = f"I did not change the manuscript. {note}"
            extra = [note]
            extra.extend(salvage_logs[:6])
            activity = _improve_activity_for_turn(
                prior_draft=prior_draft,
                section=section,
                draft=draft,
                draft_changed=draft_changed,
                assistant_message=assistant_message,
                extra_discrepancies=extra,
            )
            return ProposalSectionImproveResponse(
                section=section,
                draft=draft,
                research=_slim_research(research) or research,
                assistantMessage=assistant_message,
                draftChanged=draft_changed,
                suggestedFix=None,
                agentActivity=activity,
            )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Section improve failed for %s", rfp_id)
        if prior_draft and prior_draft.sections:
            research = await aget_research_cache(rfp_id) or ProposalResearchCache(
                rfpId=rfp_id
            )
            draft, draft_changed, salvage_logs = await _salvage_draft_after_improve_failure(
                rfp_id, prior_draft=prior_draft, research=research
            )
            section = _section_by_id(draft, section_id) or draft.sections[0]
            if draft_changed:
                note = (
                    "The rewrite did not finish, but fabricated roster names and "
                    "in-manuscript bios were replaced with designer-note PDF stubs."
                )
            else:
                note = (
                    "This turn did not finish. The manuscript was left unchanged — "
                    "try again or rephrase the instruction."
                )
            extra = [note]
            extra.extend(salvage_logs[:6])
            activity = _improve_activity_for_turn(
                prior_draft=prior_draft,
                section=section,
                draft=draft,
                draft_changed=draft_changed,
                assistant_message=note,
                extra_discrepancies=extra,
            )
            return ProposalSectionImproveResponse(
                section=section,
                draft=draft,
                research=_slim_research(research) or research,
                assistantMessage=note,
                draftChanged=draft_changed,
                suggestedFix=None,
                agentActivity=activity,
            )
        raise HTTPException(
            status_code=502,
            detail=f"Section improve failed: {exc}",
        ) from exc

    suggested_payload = None
    if suggested_fix is not None:
        suggested_payload = ProposalSuggestedFix(
            sectionId=suggested_fix.section_id,
            instruction=suggested_fix.instruction,
            summary=suggested_fix.summary,
            sectionTitle=suggested_fix.section_title,
        )

    activity = _improve_activity_for_turn(
        prior_draft=prior_draft,
        section=section,
        draft=draft,
        draft_changed=draft_changed,
        assistant_message=assistant_message,
    )
    return ProposalSectionImproveResponse(
        section=section,
        draft=draft,
        research=_slim_research(research) or research,
        assistantMessage=assistant_message,
        draftChanged=draft_changed,
        suggestedFix=suggested_payload,
        agentActivity=activity,
    )


@router.post(
    "/{rfp_id}/proposal/phase-4-review",
)
async def phase4_presubmit_review_endpoint(rfp_id: str) -> JSONResponse:
    """Start Stage 4 pre-submit review in the background; poll GET /proposal for completion."""

    async def work() -> None:
        await run_phase4_presubmit_review(rfp_id)

    return await _enqueue_pipeline_phase(rfp_id, "phase-4-review", work)


@router.post(
    "/{rfp_id}/proposal/phase-4-auto-fix",
    response_model=ProposalPhase4AutoFixResponse,
)
async def phase4_presubmit_autofix_endpoint(
    rfp_id: str,
    request: Request,
    body: PreSubmitAutoFixRequest | None = None,
) -> ProposalPhase4AutoFixResponse:
    """AI + Supermemory repair for all review findings — cancellable."""
    use_llm = body.use_llm if body else True

    async def should_cancel() -> bool:
        return await request.is_disconnected()

    try:
        review, research, draft, auto_fix = await run_phase4_presubmit_autofix(
            rfp_id,
            use_llm=use_llm,
            should_cancel=should_cancel,
        )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Pre-submit auto-fix failed: {exc}",
        ) from exc

    slim = _slim_research(research) or research
    return ProposalPhase4AutoFixResponse(
        review=review,
        research=slim,
        draft=draft,
        auto_fix=auto_fix,
    )


@router.post(
    "/{rfp_id}/proposal/phase-4-finalize-gaps",
    response_model=ProposalPhase4Response,
)
async def phase4_finalize_gaps_endpoint(rfp_id: str) -> ProposalPhase4Response:
    """Final editor: Supermemory gap-fill, then owner-assigned MANUAL FILL flags."""
    try:
        review, research, draft = await run_phase4_finalize_gaps(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gap finalize failed: {exc}",
        ) from exc

    await asave_proposal_draft(draft)
    slim = _slim_research(research) or research
    return ProposalPhase4Response(review=review, research=slim, draft=draft)


@router.post(
    "/{rfp_id}/proposal/fulfill-rfp-gaps",
)
async def fulfill_rfp_gaps_endpoint(
    rfp_id: str,
    body: PreSubmitAutoFixRequest | None = None,
) -> JSONResponse:
    """Start Complete & clean in the background; poll GET /proposal until done.

    Must not hold the HTTP request open — proxies return HTML/empty and the
    client shows “Invalid response from fulfill RFP gaps.” Disconnect must
    not cancel the job (Stop is POST /stop).
    """
    from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

    use_llm = body.use_llm if body else True
    mode = (body.mode if body and getattr(body, "mode", None) else None) or "full"

    async def work() -> None:
        await run_fulfill_rfp_gaps(rfp_id, use_llm=use_llm, mode=mode)

    # Safety net so "Complete & clean draft" can never freeze the app forever:
    # generously above the ~19min bounded worst case of the final steps, well
    # below the frontend's 90-minute poll give-up (proposal-api.ts FULFILL_POLL_MAX_MS).
    # (Celery path: covered by task_time_limit in app/celery_app.py instead.)
    return await _enqueue_pipeline_phase(
        rfp_id,
        "fulfill-scan",
        work,
        timeout_sec=60 * 60,
        job_kwargs={"use_llm": use_llm, "mode": mode},
    )


@router.post(
    "/{rfp_id}/proposal/restore-snapshot",
    response_model=ProposalRestoreSnapshotResponse,
)
async def restore_proposal_snapshot_endpoint(
    rfp_id: str,
    body: ProposalRestoreSnapshotRequest,
) -> ProposalRestoreSnapshotResponse:
    from app.services.proposal_draft_archives import (
        REASON_BEFORE_ARCHIVE_RESTORE,
        archive_filled_draft,
    )
    from app.services.proposal_draft_snapshots import (
        prune_clutter_snapshots,
        restore_proposal_snapshot,
    )
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft

    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        raise HTTPException(status_code=404, detail="No proposal draft found.")
    # Durable archive (not another dropdown row) so restore cannot spam the menu.
    await archive_filled_draft(
        draft,
        reason=REASON_BEFORE_ARCHIVE_RESTORE,
        label="Before snapshot restore",
    )
    restored = restore_proposal_snapshot(draft, saved_at=body.saved_at)
    if not restored:
        raise HTTPException(
            status_code=404,
            detail=(
                "Snapshot not found or has no section content to restore. "
                "Try another Saved version, or re-improve the section to create a fresh checkpoint."
            ),
        )
    restored = prune_clutter_snapshots(restored)
    await asave_proposal_draft(restored)
    return ProposalRestoreSnapshotResponse(draft=restored)


@router.post("/{rfp_id}/proposal/export/docx")
async def export_proposal_docx(rfp_id: str) -> Response:
    """Download full proposal as Word (.docx) — same structure as in-app manuscript."""
    from app.services.proposal_docx_export import (
        ProposalDocxExportError,
        build_proposal_docx_bytes,
        build_proposal_docx_filename,
    )
    from app.services.proposal_google_doc_export import _sanitize_doc_title
    from app.services.proposal_repository import aget_proposal_draft

    try:
        if not rfp_exists(rfp_id):
            raise HTTPException(status_code=404, detail="RFP not found")

        draft = await aget_proposal_draft(rfp_id)
        if not draft or not draft.sections:
            raise HTTPException(status_code=400, detail="No proposal draft to export.")

        title = "Proposal"
        try:
            rfp = get_rfp(rfp_id)
            if rfp and rfp.title:
                title = rfp.title
        except Exception:
            pass

        doc_title = _sanitize_doc_title(f"{title} — Proposal")
        payload = build_proposal_docx_bytes(doc_title=doc_title, draft=draft)
        filename = build_proposal_docx_filename(rfp_title=title)
    except HTTPException:
        raise
    except ProposalDocxExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection dropped while exporting. Please try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Word export failed: {exc}",
        ) from exc

    encoded = quote(filename)
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{encoded}"; filename*=UTF-8\'\'{encoded}'
            ),
        },
    )


@router.post("/{rfp_id}/proposal/export/readiness-report")
async def export_readiness_report_docx(rfp_id: str) -> Response:
    """Download the Submission Readiness Report (.docx) — internal, never submitted.

    Renders from the data Complete & Scan persisted, so it never re-runs the scan.
    """
    from app.services.proposal_manual_fill_triage import triage_manual_fill_flags  # noqa: F401
    from app.services.proposal_readiness import CriterionScore, compute_readiness
    from app.services.proposal_readiness_report import (
        build_readiness_report_docx_bytes,
        build_readiness_report_filename,
    )
    from app.services.proposal_repository import aget_proposal_draft, aget_research_cache

    try:
        if not rfp_exists(rfp_id):
            raise HTTPException(status_code=404, detail="RFP not found")

        draft = await aget_proposal_draft(rfp_id)
        if not draft:
            raise HTTPException(status_code=400, detail="No proposal draft to report on.")

        stored = draft.last_fulfill_report or {}
        payload_data = stored.get("readinessReport") or {}
        if not stored.get("readiness"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "No readiness data yet. Run Complete & Scan first — the report is "
                    "built from that pass."
                ),
            )

        research = await aget_research_cache(rfp_id)
        review = getattr(research, "presubmit_review", None) if research else None
        flags = list(getattr(review, "manual_fill_flags", None) or [])

        scores = [
            CriterionScore(
                section_id=str(row.get("sectionId") or ""),
                criterion=str(row.get("criterion") or ""),
                score=int(row.get("score") or 0),
                weight=row.get("weight"),
            )
            for row in (payload_data.get("scorecard") or [])
        ]
        # Recomputed rather than read back so the number always matches the flags and
        # scorecard rendered beside it, even if the draft changed after the scan.
        readiness = compute_readiness(
            scores=scores,
            flags=flags,
            unresolved=len(payload_data.get("unverifiedClaims") or []),
        )

        title = str(payload_data.get("rfpTitle") or "") or "Proposal"
        payload = build_readiness_report_docx_bytes(
            rfp_title=title,
            readiness=readiness,
            flags=flags,
            scores=scores,
            changes=list(payload_data.get("changes") or []),
            unverified_claims=list(payload_data.get("unverifiedClaims") or []),
            unfixed=list(payload_data.get("unfixed") or []),
        )
        filename = build_readiness_report_filename(rfp_title=title)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Readiness report export failed: {exc}"
        ) from exc

    encoded = quote(filename)
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{encoded}"; filename*=UTF-8\'\'{encoded}'
            ),
        },
    )


@router.post(
    "/{rfp_id}/proposal/export/google-doc",
    response_model=ProposalGoogleDocExportResponse,
)
async def export_proposal_google_doc(rfp_id: str) -> ProposalGoogleDocExportResponse:
    """Create a Google Doc with the full ordered proposal manuscript."""
    from app.services.proposal_google_doc_export import (
        ProposalGoogleDocExportError,
        export_proposal_to_google_doc,
    )
    from app.services.proposal_repository import aget_proposal_draft

    try:
        if not rfp_exists(rfp_id):
            raise HTTPException(status_code=404, detail="RFP not found")

        draft = await aget_proposal_draft(rfp_id)
        if not draft or not draft.sections:
            raise HTTPException(status_code=400, detail="No proposal draft to export.")

        title = "Proposal"
        try:
            rfp = get_rfp(rfp_id)
            if rfp and rfp.title:
                title = rfp.title
        except Exception:
            # Title is nice-to-have; don't fail export if Supabase briefly drops.
            pass

        result = await export_proposal_to_google_doc(rfp_title=title, draft=draft)

        from datetime import datetime, timezone

        from app.services.proposal_repository import asave_proposal_draft

        draft.google_doc_url = result["documentUrl"]
        draft.google_doc_id = result["documentId"]
        draft.google_doc_exported_at = datetime.now(timezone.utc).isoformat()
        try:
            await asave_proposal_draft(draft)
        except Exception:
            # Export succeeded; URL persistence is best-effort.
            pass
    except HTTPException:
        raise
    except ProposalGoogleDocExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection dropped while exporting. Please try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Google Doc export failed: {exc}",
        ) from exc

    return ProposalGoogleDocExportResponse(
        document_id=result["documentId"],
        document_url=result["documentUrl"],
        title=result["title"],
        section_count=result["sectionCount"],
        instruction_leaks=result.get("instructionLeaks") or [],
    )


class ProposalKeyPersonasRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    selected_persona_ids: list[str] = Field(..., alias="selectedPersonaIds")


@router.get("/{rfp_id}/proposal/key-personas")
@router.get("/{rfp_id}/key-personas")
async def get_proposal_key_personas(rfp_id: str) -> dict[str, object]:
    from app.services import team_personas_service
    from app.services.proposal_repository import aget_proposal_draft

    all_personas = await team_personas_service.get_all_key_personas()
    selected_ids: list[str] = []
    try:
        draft = await aget_proposal_draft(rfp_id)
        if draft and draft.selected_key_personas:
            retired_ids = {
                str(p.get("id") or "")
                for p in all_personas
                if p.get("retired")
            }
            selected_ids = [
                pid
                for pid in draft.selected_key_personas
                if pid not in retired_ids
            ]
    except Exception:
        pass

    return {
        "total": len(all_personas),
        "personas": all_personas,
        "selectedPersonaIds": selected_ids,
    }


@router.post("/{rfp_id}/proposal/key-personas")
@router.post("/{rfp_id}/key-personas")
async def save_proposal_key_personas(
    rfp_id: str, payload: ProposalKeyPersonasRequest
) -> dict[str, object]:
    from datetime import datetime, timezone
    from app.services import team_personas_service
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
    from app.models.proposal import ProposalDraft

    logger = logging.getLogger(__name__)

    all_personas = await team_personas_service.get_all_key_personas()
    retired_ids = {
        str(p.get("id") or "")
        for p in all_personas
        if p.get("retired")
    }
    selected_ids = [
        pid for pid in payload.selected_persona_ids if pid not in retired_ids
    ]

    draft = await aget_proposal_draft(rfp_id)
    now = datetime.now(timezone.utc).isoformat()
    prior = draft
    if not draft:
        draft = ProposalDraft(
            rfp_id=rfp_id,
            sections=[],
            updated_at=now,
            selected_key_personas=selected_ids,
        )
    else:
        draft = draft.model_copy(
            update={
                "selected_key_personas": selected_ids,
                "updated_at": now,
            }
        )

    # Keep Team Bios tabs in lockstep with the Key Personas picker — add stubs
    # for new picks, drop tabs for people who were unchecked. Do this in-request
    # (no sections-1-3 job): a background rebuild raced with client autosave and
    # merged the old bios back in.
    bios_synced = False
    if draft.sections:
        try:
            from app.services import team_personas_service
            from app.services.proposal_chat_structure import (
                sync_draft_bios_to_key_personas,
            )
            from app.services.proposal_draft_snapshots import (
                push_before_structure_change_snapshot,
            )

            all_personas = await team_personas_service.get_all_key_personas()
            by_id = {p["id"]: p for p in all_personas}
            selected_personas = [
                by_id[pid]
                for pid in selected_ids
                if pid in by_id
            ]
            synced, bios_synced = sync_draft_bios_to_key_personas(
                draft, selected_personas
            )
            if bios_synced:
                base = prior or draft
                draft = push_before_structure_change_snapshot(
                    base, section_title="Key Personas"
                )
                draft = draft.model_copy(
                    update={
                        "sections": synced.sections,
                        "updated_at": now,
                        "selected_key_personas": selected_ids,
                    }
                )
        except Exception as exc:
            logger.warning(
                "Key Persona bio sync failed for %s: %s", rfp_id, exc
            )

    try:
        await asave_proposal_draft(draft)
        from app.services import supabase_db

        if supabase_db.use_supabase_db():
            note = f"Key Personas selected ({len(selected_ids)}): {', '.join(selected_ids)}"
            try:
                supabase_db._get_client().table("rfps").update(
                    {
                        "last_activity": now,
                        "last_activity_note": note[:250],
                    }
                ).or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}").execute()
            except Exception as exc:
                logger.warning(
                    "Persona save last_activity update failed for %s: %s",
                    rfp_id,
                    exc,
                )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to save Key Personas: {exc}",
        ) from exc

    return {
        "ok": True,
        "rfpId": rfp_id,
        "selectedPersonaIds": draft.selected_key_personas or [],
        "biosSynced": bios_synced,
        "draft": draft.model_dump(by_alias=True),
    }


proposals_direct_router = APIRouter(prefix="/proposals", tags=["proposals"])


@proposals_direct_router.get("/jobs/active")
async def list_active_proposal_jobs_endpoint() -> dict[str, object]:
    """Every proposal-pipeline job currently queued or running, across all
    RFPs — so a newly-queued job's UI can show what's ahead of it instead of
    just "generating..." with no explanation of why nothing is happening
    yet. Excludes Go/No-Go (tracked here too, but under its own lock — not
    what occupies a proposal-pipeline worker slot)."""
    from app.services.proposal_job_runner import list_active_proposal_jobs

    records = await list_active_proposal_jobs()
    jobs = []
    for record in records:
        if record.job_type == "go-no-go":
            continue
        rfp = get_rfp(record.rfp_id)
        jobs.append(
            {
                "rfpId": record.rfp_id,
                "title": rfp.title if rfp else record.rfp_id,
                "jobType": record.job_type,
                "status": record.status,
                "startedAt": record.started_at,
            }
        )
    return {"jobs": jobs}


@proposals_direct_router.get("/{rfp_id}/key-personas")
@proposals_direct_router.get("/{rfp_id}/proposal/key-personas")
async def get_proposal_key_personas_direct(rfp_id: str) -> dict[str, object]:
    return await get_proposal_key_personas(rfp_id)


@proposals_direct_router.post("/{rfp_id}/key-personas")
@proposals_direct_router.post("/{rfp_id}/proposal/key-personas")
async def save_proposal_key_personas_direct(
    rfp_id: str, payload: ProposalKeyPersonasRequest
) -> dict[str, object]:
    return await save_proposal_key_personas(rfp_id, payload)


