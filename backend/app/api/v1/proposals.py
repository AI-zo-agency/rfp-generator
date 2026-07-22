from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
import httpx
import re
from datetime import datetime
from urllib.parse import quote

from app.models.proposal import (
    ProposalDraft,
    ProposalFulfillGapsResponse,
    ProposalGoogleDocExportResponse,
    ProposalGenerateResponse,
    ProposalPhase4AutoFixResponse,
    ProposalPhase4Response,
    ProposalPricingResponse,
    ProposalResearchCache,
    ProposalRestoreSnapshotRequest,
    ProposalRestoreSnapshotResponse,
    ProposalSectionImproveResponse,
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


async def _enqueue_pipeline_phase(
    rfp_id: str,
    phase: str,
    work: Callable[[], Awaitable[Any]],
) -> JSONResponse:
    """Start a long phase in-process and return immediately (202).

    Clients poll GET /proposal (checkpoint + draft) until the phase completes.
    Does not cancel on HTTP disconnect — Stop is explicit via POST /stop.
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

    from app.services.proposal_pipeline_checkpoint import record_phase_started

    # Mark in-progress before returning so the first poll sees the phase.
    await record_phase_started(rfp_id, phase)

    async def _run() -> Any:
        async with pipeline_phase(rfp_id, phase):
            return await work()

    record = await start_proposal_job(rfp_id, phase, _run)
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
        from app.services.proposal_draft_snapshots import prune_clutter_snapshots
        from app.services.proposal_repository import asave_proposal_draft

        pruned = prune_clutter_snapshots(draft)
        if [s.saved_at for s in (pruned.snapshots or [])] != [
            s.saved_at for s in (draft.snapshots or [])
        ]:
            await asave_proposal_draft(pruned)
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


@router.post("/{rfp_id}/proposal/stop")
async def stop_proposal_generation_endpoint(rfp_id: str) -> dict[str, object]:
    """Request cooperative stop — ends current LLM/Supermemory work and saves checkpoint."""
    from app.services.proposal_generation_cancel import request_generation_cancel
    from app.services.proposal_pipeline_checkpoint import record_generation_stopped

    request_generation_cancel(rfp_id)
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
async def generate_sections_1_3_endpoint(rfp_id: str) -> JSONResponse:
    """Start static Sections 1–3 in the background; poll GET /proposal for completion."""

    async def work() -> None:
        await generate_sections_1_3(rfp_id, force_regenerate=True)

    return await _enqueue_pipeline_phase(rfp_id, "sections-1-3", work)


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
    try:
        section, draft, research, _provider, assistant_message, draft_changed = await improve_proposal_section(
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
        )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Section improve failed: {exc}",
        ) from exc

    return ProposalSectionImproveResponse(
        section=section,
        draft=draft,
        research=_slim_research(research) or research,
        assistantMessage=assistant_message,
        draftChanged=draft_changed,
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
    response_model=ProposalFulfillGapsResponse,
)
async def fulfill_rfp_gaps_endpoint(
    rfp_id: str,
    request: Request,
    body: PreSubmitAutoFixRequest | None = None,
) -> ProposalFulfillGapsResponse:
    """Re-read THIS RFP, add missing closing package sections, patch uncovered gaps."""
    from app.services.proposal_disconnect_cancel import cancel_generation_on_disconnect
    from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

    use_llm = body.use_llm if body else True
    try:
        async with cancel_generation_on_disconnect(rfp_id, request):
            review, research, draft, fulfill_report = await run_fulfill_rfp_gaps(
                rfp_id, use_llm=use_llm
            )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Fulfill RFP gaps failed: {exc}",
        ) from exc

    slim = _slim_research(research) or research
    return ProposalFulfillGapsResponse(
        review=review,
        research=slim,
        draft=draft,
        fulfill_report=fulfill_report,
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
    )
