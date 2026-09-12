"""Hard monthly LLM spend cap across proposals + financial workspace.

Counts every logged USD after ``MONTHLY_LLM_BUDGET_EPOCH`` inside the current
UTC calendar month, from:

* ``llm_call_log`` — Generate / Scan / Go-No-Go / section chat / etc.
* ``financial_llm_calls`` — QB / Teamwork / agency chat + nightly AI

Enforced before every provider call via ``enforce_monthly_llm_budget``.
Set ``MONTHLY_LLM_BUDGET_USD=0`` to disable.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 2.0
_status_cache: tuple[float, dict[str, Any]] | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clear_monthly_budget_cache() -> None:
    global _status_cache
    _status_cache = None


def _parse_epoch(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        logger.warning("Invalid MONTHLY_LLM_BUDGET_EPOCH=%r — ignoring", raw[:64])
        return None


def _month_window(now: datetime) -> tuple[datetime, datetime]:
    """UTC calendar month containing ``now`` → [start, end)."""
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _sum_supabase_table(
    table: str,
    start_iso: str,
    end_iso: str,
) -> float:
    from app.services.supabase_db import _get_client

    client = _get_client()
    total = 0.0
    offset = 0
    page = 1000
    while True:
        result = (
            client.table(table)
            .select("cost_usd")
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .range(offset, offset + page - 1)
            .execute()
        )
        batch = result.data or []
        if not batch:
            break
        total += sum(float(r.get("cost_usd") or 0) for r in batch)
        if len(batch) < page:
            break
        offset += page
    return total


def _is_financial_node_name(node_name: str) -> bool:
    try:
        from app.services.llm import _is_financial_node

        return bool(_is_financial_node(node_name))
    except Exception:  # noqa: BLE001
        return False


def _sum_llm_call_log_split(start_iso: str, end_iso: str) -> tuple[float, float]:
    """Split proposal ledger into (proposal_usd, misfiled_financial_usd).

    Older financial ``chat_json`` calls wrote into ``llm_call_log``. Those rows
    still count toward the monthly cap but must show under Finance, not Proposals.
    """
    from app.services import supabase_db as sb
    from app.services.llm_call_log import ensure_llm_call_log_table
    from app.services.rfp_repository import _connect

    ensure_llm_call_log_table()
    proposal = 0.0
    financial = 0.0
    if sb.use_supabase_db():
        from app.services.supabase_db import _get_client

        client = _get_client()
        offset = 0
        page = 1000
        while True:
            result = (
                client.table("llm_call_log")
                .select("cost_usd,node_name")
                .gte("created_at", start_iso)
                .lt("created_at", end_iso)
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = result.data or []
            if not batch:
                break
            for row in batch:
                cost = float(row.get("cost_usd") or 0)
                node = str(row.get("node_name") or "")
                if _is_financial_node_name(node):
                    financial += cost
                else:
                    proposal += cost
            if len(batch) < page:
                break
            offset += page
        return proposal, financial

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT cost_usd, node_name
            FROM llm_call_log
            WHERE created_at >= ? AND created_at < ?
            """,
            (start_iso, end_iso),
        ).fetchall()
        for cost_usd, node_name in rows:
            cost = float(cost_usd or 0)
            if _is_financial_node_name(str(node_name or "")):
                financial += cost
            else:
                proposal += cost
    return proposal, financial


def _sum_llm_call_log_usd(start_iso: str, end_iso: str) -> float:
    """Total USD in llm_call_log (proposal + any misfiled financial rows)."""
    proposal, financial = _sum_llm_call_log_split(start_iso, end_iso)
    return proposal + financial


def _sum_financial_llm_calls_usd(start_iso: str, end_iso: str) -> float:
    """QB / Teamwork / agency chat ledger. Missing table → 0 (not a hard fail)."""
    from app.services import supabase_db as sb

    if not sb.use_supabase_db():
        # Financial workspace is Supabase-only today; no local twin to sum.
        return 0.0
    try:
        return _sum_supabase_table("financial_llm_calls", start_iso, end_iso)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        # Table not migrated yet — treat as zero so we still enforce on proposals.
        if "PGRST205" in message or (
            "Could not find" in message and "financial_llm_calls" in message
        ):
            logger.warning(
                "financial_llm_calls missing — monthly budget counts proposals only"
            )
            return 0.0
        logger.warning(
            "monthly budget financial_llm_calls sum failed: %s", message[:240]
        )
        raise


def get_monthly_budget_status(*, use_cache: bool = True) -> dict[str, Any]:
    """Return spent / limit / remaining for the current UTC month (post-epoch)."""
    global _status_cache
    limit = float(getattr(settings, "monthly_llm_budget_usd", 0.0) or 0.0)
    enabled = limit > 0
    now = _utcnow()
    month_start, month_end = _month_window(now)
    epoch = _parse_epoch(str(getattr(settings, "monthly_llm_budget_epoch", "") or ""))
    window_start = month_start
    if epoch is not None and epoch > window_start:
        window_start = epoch

    if use_cache and _status_cache is not None:
        cached_at, cached = _status_cache
        if time.monotonic() - cached_at < _CACHE_TTL_S:
            return dict(cached)

    proposal = 0.0
    financial = 0.0
    read_error: str | None = None
    if enabled:
        try:
            start_s, end_s = _iso(window_start), _iso(month_end)
            proposal_only, misfiled_financial = _sum_llm_call_log_split(start_s, end_s)
            financial_table = float(_sum_financial_llm_calls_usd(start_s, end_s))
            proposal = float(proposal_only)
            financial = float(financial_table) + float(misfiled_financial)
        except Exception as exc:  # noqa: BLE001
            # Stricter: cannot read ledger → treat as blocked.
            read_error = str(exc)[:200]
            proposal = limit
            financial = 0.0

    spent = round(proposal + financial, 6)
    remaining = max(0.0, round(limit - spent, 6)) if enabled else 0.0
    blocked = bool(enabled and spent >= limit)
    status: dict[str, Any] = {
        "enabled": enabled,
        "limit_usd": round(limit, 6) if enabled else 0.0,
        "spent_usd": spent if enabled else 0.0,
        "remaining_usd": remaining,
        "blocked": blocked,
        "proposal_spent_usd": round(proposal, 6) if enabled else 0.0,
        "financial_spent_usd": round(financial, 6) if enabled else 0.0,
        "period_start": _iso(window_start),
        "period_end": _iso(month_end),
        "epoch": _iso(epoch) if epoch else "",
        "timezone": "UTC",
        "read_error": read_error,
    }
    _status_cache = (time.monotonic(), status)
    return dict(status)


def enforce_monthly_llm_budget() -> None:
    """Raise ``LlmError(429)`` when monthly spend is at/over the hard cap."""
    limit = float(getattr(settings, "monthly_llm_budget_usd", 0.0) or 0.0)
    if limit <= 0:
        return
    clear_monthly_budget_cache()  # never trust a stale allow right before a call
    status = get_monthly_budget_status(use_cache=False)
    if not status.get("blocked"):
        return
    from app.services.llm import LlmError

    spent = float(status.get("spent_usd") or 0)
    remaining = float(status.get("remaining_usd") or 0)
    err = status.get("read_error")
    if err:
        raise LlmError(
            (
                f"Monthly LLM budget guard cannot read cost ledgers ({err}). "
                f"Hard cap is ${limit:.2f}/month — refusing AI calls until spend "
                "can be verified."
            ),
            status_code=429,
        )
    raise LlmError(
        (
            f"Monthly LLM budget exceeded: ${spent:.2f} spent "
            f"(cap ${limit:.2f}/month, ${remaining:.2f} remaining). "
            "All AI features are paused until next UTC month "
            f"(resets {status.get('period_end', '')})."
        ),
        status_code=429,
    )


def raise_http_if_monthly_budget_blocked() -> None:
    """FastAPI preflight — same rule, HTTP 429."""
    from fastapi import HTTPException

    try:
        enforce_monthly_llm_budget()
    except Exception as exc:  # noqa: BLE001
        from app.services.llm import LlmError

        if isinstance(exc, LlmError) and exc.status_code == 429:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        raise
