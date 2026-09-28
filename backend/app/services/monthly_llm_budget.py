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
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# UI polls every ~2 min; keep server cache long enough that concurrent
# TopBar/Sidebar/Analytics hits don't each re-scan Supabase.
_CACHE_TTL_S = 60.0
_status_cache: tuple[float, dict[str, Any]] | None = None
_history_cache: tuple[float, list[dict[str, Any]]] | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clear_monthly_budget_cache() -> None:
    global _status_cache, _history_cache
    _status_cache = None
    _history_cache = None


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


def _week_window(now: datetime) -> tuple[datetime, datetime]:
    """UTC ISO week (Mon 00:00 → next Mon) containing ``now``."""
    day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    start = day - timedelta(days=day.weekday())  # Monday
    end = start + timedelta(days=7)
    return start, end


def _day_window(now: datetime) -> tuple[datetime, datetime]:
    """UTC calendar day containing ``now`` → [start, next day)."""
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _clip_start(window_start: datetime, epoch: datetime | None) -> datetime:
    if epoch is not None and epoch > window_start:
        return epoch
    return window_start


def _unpack_period(
    parts: tuple[Any, ...],
) -> tuple[float, float, float, list[dict[str, Any]], float]:
    """Accept 3-, 4-, or 5-tuples so older test doubles still load.

    Real ``_period_spend`` returns
    (proposal, financial, total, by_user, outreach).
    """
    proposal = float(parts[0]) if parts else 0.0
    financial = float(parts[1]) if len(parts) > 1 else 0.0
    total = float(parts[2]) if len(parts) > 2 else proposal + financial
    by_user = parts[3] if len(parts) > 3 and isinstance(parts[3], list) else []
    outreach = float(parts[4]) if len(parts) > 4 else 0.0
    return proposal, financial, total, by_user, outreach


def _period_spend(
    start: datetime,
    end: datetime,
) -> tuple[float, float, float, list[dict[str, Any]], float]:
    """Return (proposal, financial, total, proposal_by_user, outreach)."""
    start_s, end_s = _iso(start), _iso(end)
    proposal_only, misfiled_financial, outreach, by_user = _sum_llm_call_log_split(
        start_s, end_s
    )
    financial_table = float(_sum_financial_llm_calls_usd(start_s, end_s))
    proposal = float(proposal_only)
    financial = float(financial_table) + float(misfiled_financial)
    outreach_usd = float(outreach)
    return proposal, financial, proposal + financial + outreach_usd, by_user, outreach_usd


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


# Prospect outreach calls live in llm_call_log under these node names.
_OUTREACH_LLM_NODES = frozenset({"leads_enrich", "leads_brief"})


def _is_financial_node_name(node_name: str) -> bool:
    try:
        from app.services.llm import _is_financial_node

        return bool(_is_financial_node(node_name))
    except Exception:  # noqa: BLE001
        return False


def _spend_bucket(node_name: str) -> str:
    """Ledger product: financial, outreach, or proposal (Ralph)."""
    if _is_financial_node_name(node_name):
        return "financial"
    if (node_name or "").strip() in _OUTREACH_LLM_NODES:
        return "outreach"
    return "proposal"


def _by_user_rows(totals: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {"email": email, "proposal_spent_usd": cost}
        for email, cost in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
        if cost > 0
    ]


def _sum_llm_call_log_split(
    start_iso: str, end_iso: str
) -> tuple[float, float, float, list[dict[str, Any]]]:
    """Split llm_call_log into (proposal, misfiled_financial, outreach, by_user).

    Older financial ``chat_json`` calls wrote into ``llm_call_log``. Those rows
    still count toward the monthly cap but must show under Finance, not Ralph.
    Outreach nodes (lead enrich / brief) are their own product. Proposal rows
    are grouped by ``user_email`` in the same scan.
    """
    from app.services import supabase_db as sb
    from app.services.llm_call_log import ensure_llm_call_log_table
    from app.services.rfp_repository import _connect

    ensure_llm_call_log_table()
    proposal = 0.0
    financial = 0.0
    outreach = 0.0
    by_email: dict[str, float] = {}

    def _add_user(email: str, cost: float) -> None:
        key = (email or "").strip().lower() or "(unattributed)"
        by_email[key] = round(float(by_email.get(key) or 0) + float(cost), 6)

    if sb.use_supabase_db():
        from app.services.supabase_db import _get_client

        client = _get_client()
        offset = 0
        page = 1000
        select_cols = "cost_usd,node_name,user_email"
        while True:
            try:
                result = (
                    client.table("llm_call_log")
                    .select(select_cols)
                    .gte("created_at", start_iso)
                    .lt("created_at", end_iso)
                    .range(offset, offset + page - 1)
                    .execute()
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if select_cols != "cost_usd,node_name" and (
                    "user_email" in message or "PGRST204" in message
                ):
                    select_cols = "cost_usd,node_name"
                    continue
                raise
            batch = result.data or []
            if not batch:
                break
            for row in batch:
                cost = float(row.get("cost_usd") or 0)
                bucket = _spend_bucket(str(row.get("node_name") or ""))
                if bucket == "financial":
                    financial += cost
                elif bucket == "outreach":
                    outreach += cost
                else:
                    proposal += cost
                    _add_user(str(row.get("user_email") or ""), cost)
            if len(batch) < page:
                break
            offset += page
        return proposal, financial, outreach, _by_user_rows(by_email)

    with _connect() as conn:
        cols = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(llm_call_log)").fetchall()
        }
        if "user_email" in cols:
            rows = conn.execute(
                """
                SELECT cost_usd, node_name, user_email
                FROM llm_call_log
                WHERE created_at >= ? AND created_at < ?
                """,
                (start_iso, end_iso),
            ).fetchall()
            for cost_usd, node_name, user_email in rows:
                cost = float(cost_usd or 0)
                bucket = _spend_bucket(str(node_name or ""))
                if bucket == "financial":
                    financial += cost
                elif bucket == "outreach":
                    outreach += cost
                else:
                    proposal += cost
                    _add_user(str(user_email or ""), cost)
        else:
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
                bucket = _spend_bucket(str(node_name or ""))
                if bucket == "financial":
                    financial += cost
                elif bucket == "outreach":
                    outreach += cost
                else:
                    proposal += cost
                    _add_user("", cost)
    return proposal, financial, outreach, _by_user_rows(by_email)


def _sum_llm_call_log_usd(start_iso: str, end_iso: str) -> float:
    """Total USD in llm_call_log (proposal + outreach + misfiled financial)."""
    proposal, financial, outreach, _by_user = _sum_llm_call_log_split(start_iso, end_iso)
    return proposal + financial + outreach


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
    """Return spent / limit / remaining for the current UTC month (post-epoch).

    Also includes current UTC day + ISO-week spend for the sidebar meter.
    """
    global _status_cache
    limit = float(getattr(settings, "monthly_llm_budget_usd", 0.0) or 0.0)
    day_limit = float(getattr(settings, "daily_llm_budget_usd", 5.0) or 0.0)
    enabled = limit > 0
    now = _utcnow()
    month_start, month_end = _month_window(now)
    week_start, week_end = _week_window(now)
    day_start, day_end = _day_window(now)
    epoch = _parse_epoch(str(getattr(settings, "monthly_llm_budget_epoch", "") or ""))
    window_start = _clip_start(month_start, epoch)
    week_window_start = _clip_start(week_start, epoch)
    day_window_start = _clip_start(day_start, epoch)

    if use_cache and _status_cache is not None:
        cached_at, cached = _status_cache
        if time.monotonic() - cached_at < _CACHE_TTL_S:
            return dict(cached)

    proposal = 0.0
    financial = 0.0
    outreach = 0.0
    month_total = 0.0
    week_proposal = 0.0
    week_financial = 0.0
    week_outreach = 0.0
    week_spent = 0.0
    day_proposal = 0.0
    day_financial = 0.0
    day_outreach = 0.0
    day_spent = 0.0
    by_user: list[dict[str, Any]] = []
    week_by_user: list[dict[str, Any]] = []
    read_error: str | None = None
    if enabled:
        try:
            proposal, financial, month_total, by_user, outreach = _unpack_period(
                _period_spend(window_start, month_end)
            )
            (
                week_proposal,
                week_financial,
                week_spent,
                week_by_user,
                week_outreach,
            ) = _unpack_period(_period_spend(week_window_start, week_end))
            day_proposal, day_financial, day_spent, _day_by_user, day_outreach = (
                _unpack_period(_period_spend(day_window_start, day_end))
            )
        except Exception as exc:  # noqa: BLE001
            # Stricter: cannot read ledger → treat as blocked.
            read_error = str(exc)[:200]
            logger.warning("monthly llm budget ledger read failed: %s", read_error)
            proposal = limit
            financial = 0.0
            outreach = 0.0
            month_total = limit

    spent = round(month_total, 6)
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
        "outreach_spent_usd": round(outreach, 6) if enabled else 0.0,
        "proposal_by_user": by_user if enabled else [],
        "week_proposal_by_user": week_by_user if enabled else [],
        "period_start": _iso(window_start),
        "period_end": _iso(month_end),
        "week_spent_usd": round(week_spent, 6) if enabled else 0.0,
        "week_proposal_spent_usd": round(week_proposal, 6) if enabled else 0.0,
        "week_financial_spent_usd": round(week_financial, 6) if enabled else 0.0,
        "week_outreach_spent_usd": round(week_outreach, 6) if enabled else 0.0,
        "week_period_start": _iso(week_window_start),
        "week_period_end": _iso(week_end),
        "day_limit_usd": round(day_limit, 6) if enabled and day_limit > 0 else 0.0,
        "day_spent_usd": round(day_spent, 6) if enabled else 0.0,
        "day_proposal_spent_usd": round(day_proposal, 6) if enabled else 0.0,
        "day_financial_spent_usd": round(day_financial, 6) if enabled else 0.0,
        "day_outreach_spent_usd": round(day_outreach, 6) if enabled else 0.0,
        "day_period_start": _iso(day_window_start),
        "day_period_end": _iso(day_end),
        "epoch": _iso(epoch) if epoch else "",
        "timezone": "UTC",
        "read_error": read_error,
    }
    _status_cache = (time.monotonic(), status)
    return dict(status)


def _week_label(start: datetime, end_exclusive: datetime) -> str:
    last = end_exclusive - timedelta(days=1)
    if start.month == last.month and start.year == last.year:
        return f"{start.strftime('%b')} {start.day}–{last.day}"
    return f"{start.strftime('%b')} {start.day}–{last.strftime('%b')} {last.day}"


def list_prior_weeks(*, count: int = 8) -> list[dict[str, Any]]:
    """Completed UTC weeks before the current one, newest first.

    Only weeks with spend are returned. The open week stays on the live meter.

    ponytail: one ledger scan per week (at most 8). On-demand history only;
    fold into a single windowed scan if opening History gets slow.
    """
    global _history_cache
    if _history_cache is not None:
        cached_at, cached = _history_cache
        if time.monotonic() - cached_at < _CACHE_TTL_S:
            return [dict(row) for row in cached]

    now = _utcnow()
    week_start, _week_end = _week_window(now)
    epoch = _parse_epoch(str(getattr(settings, "monthly_llm_budget_epoch", "") or ""))
    rows: list[dict[str, Any]] = []
    cursor = week_start
    logger.info(
        "weekly llm cost history start count=%s week_start=%s",
        count,
        _iso(week_start),
    )
    for _ in range(max(0, count)):
        start = cursor - timedelta(days=7)
        end = cursor
        if epoch is not None and end <= epoch:
            break
        window_start = _clip_start(start, epoch)
        if window_start >= end:
            break
        proposal, financial, total, _users, outreach = _unpack_period(
            _period_spend(window_start, end)
        )
        if total > 0:
            rows.append(
                {
                    "period_start": _iso(window_start),
                    "period_end": _iso(end),
                    "label": _week_label(start, end),
                    "spent_usd": round(total, 6),
                    "proposal_spent_usd": round(proposal, 6),
                    "financial_spent_usd": round(financial, 6),
                    "outreach_spent_usd": round(outreach, 6),
                }
            )
        cursor = start
    logger.info("weekly llm cost history built week_count=%s", len(rows))
    _history_cache = (time.monotonic(), rows)
    return [dict(row) for row in rows]


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
