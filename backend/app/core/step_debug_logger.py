"""Structured pipeline step traces for proposal generation.

Writes one JSON line per event to `backend/logs/proposal_step_trace.log` and
mirrors a short line to the standard app logger so uvicorn/console shows progress.

Design goals:
- Never break the app (file/handler failures → NullHandler)
- No secrets / full manuscript text — counts, ids, durations, reasons only
- Auto-inject run_id / phase / step from contextvars when set
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOGGER_NAME = "proposal_step_trace"
_LOG_FILE = _LOG_DIR / "proposal_step_trace.log"
_MIRROR_LOGGER = logging.getLogger("app.pipeline_trace")

_LOGGER: logging.Logger | None = None

_pipeline_run_id: ContextVar[str] = ContextVar("pipeline_run_id", default="")
_pipeline_phase: ContextVar[str] = ContextVar("pipeline_phase", default="")
_pipeline_step: ContextVar[str] = ContextVar("pipeline_step", default="")
_pipeline_rfp_id: ContextVar[str] = ContextVar("pipeline_rfp_id", default="")


def get_pipeline_run_id() -> str:
    return _pipeline_run_id.get() or ""


def get_pipeline_phase() -> str:
    return _pipeline_phase.get() or ""


def get_pipeline_step() -> str:
    return _pipeline_step.get() or ""


def get_pipeline_rfp_id() -> str:
    return _pipeline_rfp_id.get() or ""


def resolve_pipeline_node_name(explicit: str | None = None) -> str:
    """Best node label for cost logging when a call site omits node_name."""
    name = (explicit or "").strip()
    if name:
        return name
    step = get_pipeline_step()
    phase = get_pipeline_phase()
    if step and phase:
        return f"{phase}:{step}"
    if step:
        return step
    if phase:
        return phase
    return "unknown"

_VERIFY_RE = re.compile(r"\[VERIFY:", re.I)
_DRAFT_FAILED_RE = re.compile(
    r"section drafting failed|needs manual regeneration|writer returned empty prose",
    re.I,
)
_INSUFFICIENT_EV_RE = re.compile(r"insufficient evidence in corpus", re.I)
_MANUAL_FILL_RE = re.compile(r"\[MANUAL\s*FILL", re.I)


def _configure_logger() -> logging.Logger:
    """Best-effort file logger.

    Must never break the app: if logs/ is not writable or handler setup fails,
    we fall back to a NullHandler.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    except Exception:  # noqa: BLE001
        logger.addHandler(logging.NullHandler())
    finally:
        logger.propagate = False
    return logger


def _get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = _configure_logger()
    return _LOGGER


def get_pipeline_run_id() -> str:
    return _pipeline_run_id.get() or ""


def get_pipeline_phase() -> str:
    return _pipeline_phase.get() or ""


def classify_section_outcome(content: str | None) -> str:
    """Coarse outcome bucket for a section body (no PII / full text)."""
    text = (content or "").strip()
    if not text:
        return "empty"
    if _DRAFT_FAILED_RE.search(text) and len(text) < 400:
        return "draft_failed"
    if _INSUFFICIENT_EV_RE.search(text) and len(text) < 600:
        return "insufficient_evidence"
    if text.startswith("[VERIFY:") and len(text) < 600:
        return "verify_stub"
    verify_n = len(_VERIFY_RE.findall(text))
    if verify_n >= 5 or (verify_n >= 2 and len(text) < 900):
        return "verify_heavy"
    if _MANUAL_FILL_RE.search(text):
        return "manual_fill"
    return "ok"


def summarize_sections(sections: Any) -> dict[str, Any]:
    """Aggregate section quality signals for pipeline traces."""
    items: list[Any]
    if sections is None:
        items = []
    elif isinstance(sections, list):
        items = sections
    else:
        items = list(sections)

    by_outcome: dict[str, int] = {}
    problem_ids: list[str] = []
    problem_titles: list[str] = []
    total_chars = 0
    total_verify = 0

    for sec in items:
        if isinstance(sec, dict):
            sid = str(sec.get("id") or "")
            title = str(sec.get("title") or sid)
            content = str(sec.get("content") or "")
        else:
            sid = str(getattr(sec, "id", "") or "")
            title = str(getattr(sec, "title", "") or sid)
            content = str(getattr(sec, "content", "") or "")

        outcome = classify_section_outcome(content)
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        total_chars += len(content)
        total_verify += len(_VERIFY_RE.findall(content))
        if outcome != "ok":
            if sid:
                problem_ids.append(sid)
            if title:
                problem_titles.append(title[:80])

    return {
        "section_count": len(items),
        "by_outcome": by_outcome,
        "ok_count": by_outcome.get("ok", 0),
        "problem_count": len(items) - by_outcome.get("ok", 0),
        "problem_ids": problem_ids[:40],
        "problem_titles": problem_titles[:20],
        "total_chars": total_chars,
        "verify_tag_count": total_verify,
    }


def summarize_budget(budget: Any) -> dict[str, Any]:
    """Counts/ids only from a ProposalBudget-like object."""
    if budget is None:
        return {"present": False}

    line_items = getattr(budget, "line_items", None) or []
    bound = 0
    manual = 0
    unbound = 0
    for item in line_items:
        source = getattr(item, "source_rate_id", None)
        if source is None and isinstance(item, dict):
            source = item.get("sourceRateId") or item.get("source_rate_id")
        is_manual = getattr(item, "is_manual_fill", None)
        if is_manual is None and isinstance(item, dict):
            is_manual = item.get("isManualFill") or item.get("is_manual_fill")
        if is_manual:
            manual += 1
        elif source:
            bound += 1
        else:
            unbound += 1

    flags = getattr(budget, "pricing_flags", None) or []
    return {
        "present": True,
        "line_items": len(line_items),
        "bound_lines": bound,
        "manual_fill_lines": manual,
        "unbound_lines": unbound,
        "revenue": getattr(budget, "agency_revenue_estimate", None),
        "lump_sum": getattr(budget, "lump_sum_total", None),
        "fee_subtotal": getattr(budget, "agency_fee_subtotal", None),
        "pricing_tier": str(getattr(budget, "pricing_tier", "") or ""),
        "fee_structure": str(getattr(budget, "fee_structure", "") or "")[:80],
        "confidence": getattr(budget, "confidence", None),
        "pricing_flag_count": len(flags),
        "commission_rate": getattr(budget, "commission_rate", None),
        "client_media_passthrough": getattr(budget, "client_media_passthrough", None),
    }


def step_trace(event: str, rfp_id: str | None = None, **fields: Any) -> None:
    """Write one JSON line per event to `backend/logs/` for debugging."""
    # Prefer explicit args; fall back to pipeline context, then LLM call context.
    run_id = fields.pop("run_id", None) or get_pipeline_run_id()
    phase = fields.pop("phase", None) or get_pipeline_phase()
    step = fields.pop("step", None) or _pipeline_step.get() or ""
    resolved_rfp = rfp_id if rfp_id is not None else (_pipeline_rfp_id.get() or None)

    if not run_id or not resolved_rfp:
        try:
            from app.services.llm_call_context import get_llm_rfp_id, get_llm_run_id

            if not run_id:
                run_id = get_llm_run_id() or ""
            if not resolved_rfp:
                resolved_rfp = get_llm_rfp_id() or None
        except Exception:  # noqa: BLE001
            pass

    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "event": event,
        **fields,
    }
    if resolved_rfp is not None:
        payload["rfp_id"] = resolved_rfp
    if run_id:
        payload["run_id"] = run_id
    if phase:
        payload["phase"] = phase
    if step:
        payload["step"] = step

    try:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001
        line = json.dumps(
            {
                "ts": payload["ts"],
                "event": event,
                "rfp_id": resolved_rfp,
                "run_id": run_id,
                "error": "payload_serialize_failed",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    _get_logger().info(line)

    # Short console mirror — enough to follow the run without opening the file.
    try:
        status = fields.get("status") or fields.get("outcome") or ""
        duration = fields.get("duration_ms")
        extras: list[str] = []
        if status:
            extras.append(str(status))
        if duration is not None:
            extras.append(f"{duration}ms")
        for key in (
            "section_id",
            "section_count",
            "problem_count",
            "line_items",
            "findings",
            "critical",
            "reason",
        ):
            if key in fields and fields[key] is not None:
                extras.append(f"{key}={fields[key]}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        _MIRROR_LOGGER.info(
            "[pipeline] %s%s%s",
            event,
            f" rfp={resolved_rfp}" if resolved_rfp else "",
            suffix,
        )
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def pipeline_run(
    *,
    rfp_id: str,
    run_id: str,
    **meta: Any,
) -> Iterator[None]:
    """Wrap an entire full-proposal (or phase API) run."""
    tokens: list[tuple[ContextVar[str], Token[str]]] = [
        (_pipeline_run_id, _pipeline_run_id.set(run_id or "")),
        (_pipeline_rfp_id, _pipeline_rfp_id.set(rfp_id or "")),
        (_pipeline_phase, _pipeline_phase.set("pipeline")),
        (_pipeline_step, _pipeline_step.set("")),
    ]
    started = time.perf_counter()
    step_trace("pipeline_start", rfp_id=rfp_id, run_id=run_id, **meta)
    try:
        yield
    except Exception as exc:
        step_trace(
            "pipeline_failed",
            rfp_id=rfp_id,
            run_id=run_id,
            status="error",
            error_type=exc.__class__.__name__,
            error_message=str(exc)[:300],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    else:
        step_trace(
            "pipeline_complete",
            rfp_id=rfp_id,
            run_id=run_id,
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


@contextmanager
def pipeline_phase(
    phase: str,
    *,
    rfp_id: str | None = None,
    **meta: Any,
) -> Iterator[None]:
    """Wrap one pipeline phase; logs start/end + duration + error type."""
    prev_phase = _pipeline_phase.get()
    token = _pipeline_phase.set(phase)
    started = time.perf_counter()
    resolved_rfp = rfp_id if rfp_id is not None else (_pipeline_rfp_id.get() or None)
    step_trace(
        f"{phase}_start",
        rfp_id=resolved_rfp,
        phase=phase,
        **meta,
    )
    try:
        yield
    except Exception as exc:
        step_trace(
            f"{phase}_failed",
            rfp_id=resolved_rfp,
            phase=phase,
            status="error",
            error_type=exc.__class__.__name__,
            error_message=str(exc)[:300],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    else:
        # Callers may emit a richer *_complete event; still emit a duration marker.
        step_trace(
            f"{phase}_end",
            rfp_id=resolved_rfp,
            phase=phase,
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        _pipeline_phase.reset(token)
        if prev_phase and not _pipeline_phase.get():
            # Restore prior phase when nested.
            pass


@contextmanager
def pipeline_step(step: str, **meta: Any) -> Iterator[None]:
    """Wrap a sub-step inside a phase."""
    token = _pipeline_step.set(step)
    started = time.perf_counter()
    step_trace(f"step_{step}_start", step=step, **meta)
    try:
        yield
    except Exception as exc:
        step_trace(
            f"step_{step}_failed",
            step=step,
            status="error",
            error_type=exc.__class__.__name__,
            error_message=str(exc)[:300],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    else:
        step_trace(
            f"step_{step}_end",
            step=step,
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        _pipeline_step.reset(token)
