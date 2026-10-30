"""Runs for the activity ledger: one user click = one run.

A Generate click becomes about six pipeline phases (some chained by the Celery
worker, some POSTed by the browser). They all share one *ledger run id*, owned
by whoever clicked. The ledger id is separate from `llm_call_log.run_id`
(one per phase, used by budget guards): completion rows carry the phase's LLM
run id in `metadata.llm_run_id` so the ledger can sum cost without touching
that column.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.llm_call_context import normalize_user_email, reset_llm_user_email, set_llm_user_email
from app.services.user_activity import emit_activity, list_activity

logger = logging.getLogger(__name__)

# A later pipeline phase started within this long after an earlier one still
# belongs to the same Generate click.
CHAIN_WINDOW = timedelta(hours=12)

_ledger_run_id: ContextVar[str] = ContextVar("ledger_run_id", default="")
_ledger_llm_run_id: ContextVar[str] = ContextVar("ledger_llm_run_id", default="")


def new_ledger_run_id() -> str:
    return str(uuid4())


def get_ledger_run_id() -> str:
    return _ledger_run_id.get() or ""


def get_ledger_llm_run_id() -> str:
    return _ledger_llm_run_id.get() or ""


@contextmanager
def ledger_run_scope(
    *, ledger_run_id: str, owner_email: str = "", llm_run_id: str = ""
) -> Iterator[None]:
    """Everything emitted inside belongs to this run and (when given) this owner.

    Wrap the whole job, *outside* `pipeline_phase`: its completed/failed rows
    are written after the inner LLM context has ended, so the owner has to be
    set out here or the row is attributed to `system`.
    """
    run_token = _ledger_run_id.set(ledger_run_id or "")
    llm_token = _ledger_llm_run_id.set(llm_run_id or "")
    email = normalize_user_email(owner_email)
    email_token = set_llm_user_email(email) if email else None
    try:
        yield
    finally:
        if email_token is not None:
            reset_llm_user_email(email_token)
        _ledger_llm_run_id.reset(llm_token)
        _ledger_run_id.reset(run_token)


def _latest_started_row(rfp_id: str) -> dict | None:
    items = list_activity(
        workspace="rfp",
        limit=1,
        action="proposal.phase_started",
        entity_id=rfp_id,
        with_stats=False,
    )["items"]
    return items[0] if items else None


def latest_ledger_run_id(rfp_id: str) -> str:
    """Ledger run id of the newest started phase for this RFP, or ''."""
    row = _latest_started_row(rfp_id)
    return str(row.get("run_id") or "") if row else ""


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_ledger_run_id(rfp_id: str, phase: str, *, now: datetime | None = None) -> str:
    """Run id for a phase being started by a click.

    Reuse the previous run when this is a Generate pipeline phase that comes
    later in the pipeline than the newest started phase, within 12 hours.
    Anything else (a re-run, a Review & Fix, a first phase) is a new run.
    """
    from app.services.proposal_pipeline_checkpoint import PIPELINE_PHASES

    if phase in PIPELINE_PHASES:
        row = _latest_started_row(rfp_id)
        previous = str((row.get("metadata") or {}).get("phase") or "") if row else ""
        if row and row.get("run_id") and previous in PIPELINE_PHASES:
            started = _parse_ts(str(row.get("created_at") or ""))
            moment = now or datetime.now(timezone.utc)
            if (
                started is not None
                and moment - started <= CHAIN_WINDOW
                and PIPELINE_PHASES.index(previous) < PIPELINE_PHASES.index(phase)
            ):
                return str(row["run_id"])
    return new_ledger_run_id()


def emit_phase_started(
    rfp_id: str,
    phase: str,
    *,
    title: str,
    ledger_run_id: str,
    owner_email: str,
    llm_run_id: str = "",
    chained: bool = False,
) -> None:
    phase_label = phase.replace("_", " ").replace("-", " ")
    emit_activity(
        workspace="rfp",
        action="proposal.phase_started",
        summary=f"Started proposal phase: {phase_label}",
        actor_email=owner_email or None,
        entity_type="rfp",
        entity_id=rfp_id,
        entity_label=str(title or rfp_id)[:300],
        metadata={"phase": phase, "llm_run_id": llm_run_id, "chained": chained},
        run_id=ledger_run_id or None,
        outcome="started",
    )


def emit_gonogo_outcome(
    rfp_id: str,
    *,
    title: str,
    ok: bool,
    recommendation: str | None = None,
    error: str | None = None,
) -> None:
    """Completed/failed row for a Go/No-Go run, owned by the run's scope."""
    meta: dict[str, object] = {"llm_run_id": get_ledger_llm_run_id()}
    if ok:
        action, outcome = "rfp.gonogo_completed", "completed"
        summary = f"Go/No-Go finished: {recommendation or 'unknown'}"
        meta["recommendation"] = recommendation
    else:
        action, outcome = "rfp.gonogo_failed", "failed"
        summary = f"Go/No-Go failed: {error}"
        meta["error"] = str(error or "")[:500]
    emit_activity(
        workspace="rfp",
        action=action,
        summary=summary,
        entity_type="rfp",
        entity_id=rfp_id,
        entity_label=str(title or rfp_id)[:300],
        metadata=meta,
        run_id=get_ledger_run_id() or None,
        outcome=outcome,
    )
