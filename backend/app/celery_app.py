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
}


async def _dispatch_phase(rfp_id: str, phase: str, kwargs: dict) -> None:
    import uuid

    from app.services.llm_call_context import llm_call_context
    from app.services.proposal_generation_cancel import clear_generation_cancel
    from app.services.proposal_pipeline_checkpoint import pipeline_phase

    module_path, func_name = _PHASE_DISPATCH[phase]
    module = __import__(module_path, fromlist=[func_name])
    func = getattr(module, func_name)

    clear_generation_cancel(rfp_id)
    run_id = str(uuid.uuid4())
    async with pipeline_phase(rfp_id, phase):
        with llm_call_context(rfp_id=rfp_id, run_id=run_id, node_name=phase):
            await func(rfp_id, **kwargs)


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
