"""Supabase persistence for iWorker week/month period snapshots."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.financial.iworker_period_insights import AGGREGATE_CONTRACTOR, build_period_insights
from app.services.supabase_db import _get_client

logger = logging.getLogger(__name__)

_TABLE = "iworker_period_snapshots"
_ON_CONFLICT = "spreadsheet_id,granularity,period_start,contractor"


def _rows(data: Any) -> list[dict[str, Any]]:
    if not data:
        return []
    return data if isinstance(data, list) else [data]


def _row(
    spreadsheet_id: str,
    granularity: str,
    selected: dict[str, Any],
    metrics: dict[str, Any],
    contractor: str,
    payload: dict[str, Any],
    captured_at: str,
) -> dict[str, Any]:
    return {
        "spreadsheet_id": spreadsheet_id,
        "granularity": granularity,
        "period_start": selected["start"],
        "period_end": selected["end"],
        "contractor": contractor,
        "hours": metrics.get("hours", 0),
        "spend_usd": metrics.get("spend_usd", 0),
        "scope_risk_usd": metrics.get("scope_risk_usd", 0),
        "entries_count": metrics.get("entries_count", 0),
        "active_contractors": metrics.get("active_contractors", 0),
        "payload": payload,
        "captured_at": captured_at,
    }


def upsert_period_snapshots(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    try:
        result = (
            _get_client()
            .table(_TABLE)
            .upsert(rows, on_conflict=_ON_CONFLICT)
            .execute()
        )
        count = len(_rows(result.data)) or len(rows)
        logger.info(
            "operation=upsert_period_snapshots row_count=%s upserted=%s",
            len(rows),
            count,
        )
        return count
    except Exception:
        logger.warning(
            "operation=upsert_period_snapshots status=failed row_count=%s",
            len(rows),
            exc_info=True,
        )
        return 0


def list_period_history(
    spreadsheet_id: str,
    granularity: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    result = (
        _get_client()
        .table(_TABLE)
        .select("*")
        .eq("spreadsheet_id", spreadsheet_id)
        .eq("granularity", granularity)
        .eq("contractor", AGGREGATE_CONTRACTOR)
        .order("period_start", desc=True)
        .limit(limit)
        .execute()
    )
    rows = _rows(result.data)
    logger.info(
        "operation=list_period_history spreadsheet_id=%s granularity=%s row_count=%s",
        spreadsheet_id,
        granularity,
        len(rows),
    )
    return rows


def rows_for_current_periods(
    spreadsheet_id: str,
    entries: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    captured_at: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for grain in ("week", "month"):
        insights = build_period_insights(entries, granularity=grain, now=now)
        selected = insights["selected"]
        current = insights["current"]
        out.append(
            _row(
                spreadsheet_id,
                grain,
                selected,
                current,
                AGGREGATE_CONTRACTOR,
                insights,
                captured_at,
            )
        )
        for contractor in insights["contractors"]:
            metrics = {
                "hours": contractor["hours"],
                "spend_usd": contractor["spend_usd"],
                "scope_risk_usd": contractor["scope_risk_usd"],
                "entries_count": 0,
                "active_contractors": 1,
            }
            out.append(
                _row(
                    spreadsheet_id,
                    grain,
                    selected,
                    metrics,
                    contractor["name"],
                    {"contractor": contractor},
                    captured_at,
                )
            )
    logger.info(
        "operation=rows_for_current_periods spreadsheet_id=%s row_count=%s",
        spreadsheet_id,
        len(out),
    )
    return out
