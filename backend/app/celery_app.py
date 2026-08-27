"""Celery app for proposal-pipeline and Go/No-Go background jobs.

Only used when REDIS_URL is configured (settings.celery_enabled) — local dev
without Redis stays on the in-process asyncio.create_task path in
proposal_job_runner.py. See that module for the dispatch branch and
docs/plans/twinkling-beaming-whale.md for the full migration design.
"""

from __future__ import annotations

import asyncio
import logging

from celery import Celery

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "zo_agency",
    broker=settings.redis_url or "redis://localhost:6379/0",
    backend=settings.redis_url or "redis://localhost:6379/0",
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Hard kill + graceful warning. Replaces the ad-hoc 60min asyncio.wait_for
    # that used to wrap only the fulfill-scan endpoint (proposals.py) — this
    # now applies uniformly to every phase dispatched through Celery.
    task_time_limit=3600,
    task_soft_time_limit=3540,
    worker_prefetch_multiplier=1,
)


# Phase string -> (module path, function name) for the proposal pipeline.
# Kept as a lazy string map (not direct imports) so importing this module
# never pulls in the entire proposal_generator import graph unless a task
# actually runs — matches the existing lazy-import style used throughout
# proposals.py and proposal_generator.py.
_PHASE_DISPATCH: dict[str, tuple[str, str]] = {
    "sections-1-3": ("app.services.proposal_generator", "generate_sections_1_3"),
    "phase-2": ("app.services.proposal_generator", "run_phase2_retrieval"),
    "phase-3": ("app.services.proposal_generator", "run_phase3_drafting"),
    "phase-3-6-self-edit": ("app.services.proposal_generator", "run_phase3_6_self_edit"),
    "phase-3-5-budget": ("app.services.proposal_generator", "run_phase3_5_budget"),
    "phase-4-review": ("app.services.proposal_generator", "run_phase4_presubmit_review"),
    "fulfill-scan": ("app.services.proposal_fulfill_rfp_gaps", "run_fulfill_rfp_gaps"),
    "align-rfp-outline": (
        "app.services.proposal_align_rfp_outline",
        "run_align_to_rfp_outline",
    ),
    "packet-redistribute": (
        "app.services.proposal_packet_redistribute",
        "run_packet_redistribute",
    ),
}


async def _dispatch_phase(rfp_id: str, phase: str, kwargs: dict) -> None:
    import uuid

    from app.services.llm_call_context import llm_call_context
    from app.services.proposal_generation_cancel import aclear_generation_cancel
    from app.services.proposal_pipeline_checkpoint import pipeline_phase

    if phase not in _PHASE_DISPATCH:
        known = ", ".join(sorted(_PHASE_DISPATCH))
        raise KeyError(
            f"Unknown pipeline phase {phase!r}. Restart the Celery worker so it "
            f"loads the latest _PHASE_DISPATCH. Known phases: {known}"
        )
    # Reserved for the worker orchestrator — never pass through to phase funcs.
    chain_next = kwargs.pop("chain_next", True)
    module_path, func_name = _PHASE_DISPATCH[phase]
    module = __import__(module_path, fromlist=[func_name])
    func = getattr(module, func_name)

    # AWAIT the clear so a stale cross-process cancel flag (from an earlier Stop
    # or a killed worker) is gone before pipeline_phase's first cancel check —
    # otherwise the fresh run aborts on start with no real logs.
    await aclear_generation_cancel(rfp_id)
    run_id = str(uuid.uuid4())
    async with pipeline_phase(rfp_id, phase):
        with llm_call_context(rfp_id=rfp_id, run_id=run_id, node_name=phase):
            await func(rfp_id, **kwargs)

    if chain_next:
        await _enqueue_next_generate_phase(rfp_id, phase)


async def _enqueue_next_generate_phase(rfp_id: str, completed_phase: str) -> None:
    """Keep Generate proposal moving without requiring the browser to POST next.

    The client still polls/continues; if it drops after Phase 2, Celery starts
    Phase 3 (and so on) so the run does not look mysteriously "Stopped".
    """
    from datetime import datetime, timezone

    from app.services.proposal_generation_cancel import (
        ProposalGenerationCancelled,
        check_generation_cancelled,
    )
    from app.services.proposal_pipeline_checkpoint import (
        PIPELINE_PHASES,
        _next_phase_after,
        record_phase_started,
    )

    if completed_phase not in PIPELINE_PHASES:
        return
    try:
        await check_generation_cancelled(rfp_id)
    except ProposalGenerationCancelled:
        logger.info(
            "Skip chain after %s for %s — generation cancel flag is set",
            completed_phase,
            rfp_id,
        )
        return

    next_phase = _next_phase_after(completed_phase)
    if next_phase == "complete" or next_phase not in _PHASE_DISPATCH:
        return

    # Free the per-RFP Redis lock held by this still-finishing Celery task so
    # the next phase can dispatch (otherwise start sees us as still running).
    if settings.celery_enabled:
        from app.services.proposal_job_runner import _redis_clear_job

        await _redis_clear_job(rfp_id)

    await record_phase_started(rfp_id, next_phase)
    async_result = run_pipeline_phase_task.delay(
        rfp_id, next_phase, {"chain_next": True}
    )
    if settings.celery_enabled:
        import json

        from app.services.proposal_job_runner import (
            _REDIS_KEY_PREFIX,
            _REDIS_LOCK_TTL_SEC,
        )
        from app.services.redis_client import get_redis

        started_at = datetime.now(timezone.utc).isoformat()
        await get_redis().set(
            f"{_REDIS_KEY_PREFIX}{rfp_id}",
            json.dumps(
                {
                    "job_type": next_phase,
                    "celery_task_id": async_result.id,
                    "started_at": started_at,
                }
            ),
            ex=_REDIS_LOCK_TTL_SEC,
        )
    logger.info(
        "Chained next generate phase after %s → %s for %s (task=%s)",
        completed_phase,
        next_phase,
        rfp_id,
        async_result.id,
    )


@celery_app.task(bind=True, name="proposal.run_pipeline_phase")
def run_pipeline_phase_task(self, rfp_id: str, phase: str, kwargs: dict | None = None) -> None:
    """Celery entry point — bridges to the existing async pipeline, unchanged.

    Every function in _PHASE_DISPATCH is the exact same async function
    proposals.py called via asyncio.create_task before this migration; only
    the outer dispatch mechanism changed.
    """
    try:
        asyncio.run(_dispatch_phase(rfp_id, phase, kwargs or {}))
    except Exception:
        logger.exception("Celery pipeline phase %s failed for %s", phase, rfp_id)
        raise


async def _dispatch_go_no_go(rfp_id: str) -> None:
    import uuid

    from app.services.go_no_go_service import GoNoGoError, analyze_rfp
    from app.services.llm_call_context import llm_call_context
    from app.services.rfp_repository import get_rfp, save_go_no_go_analysis

    current = get_rfp(rfp_id)
    if not current:
        raise GoNoGoError("RFP not found", status_code=404)
    with llm_call_context(rfp_id=rfp_id, run_id=str(uuid.uuid4()), node_name="go_no_go"):
        analysis = await analyze_rfp(current)
    updated = save_go_no_go_analysis(rfp_id, analysis)
    if not updated:
        raise GoNoGoError("RFP not found after save", status_code=404)


@celery_app.task(bind=True, name="proposal.run_go_no_go")
def run_go_no_go_task(self, rfp_id: str) -> None:
    try:
        asyncio.run(_dispatch_go_no_go(rfp_id))
    except Exception as exc:
        logger.exception("Celery Go/No-Go analysis failed for %s", rfp_id)
        from app.api.v1.rfps import _mark_analyze_failed

        _mark_analyze_failed(rfp_id, f"Go/No-Go analysis failed: {exc}")
        raise
