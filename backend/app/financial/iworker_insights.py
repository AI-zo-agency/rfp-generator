"""Supabase persistence for iWorker period-scoped AI briefs."""

from __future__ import annotations

import logging
from typing import Any

from app.financial.ai_insights_repository import get_insight, upsert_insight

logger = logging.getLogger(__name__)

SOURCE = "iworker"


def scope_key(granularity: str, period_start: str) -> str:
    return f"{granularity}:{period_start}"


def build_payload(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") or {}
    return {
        "brief": summary.get("leadership_brief_text") or "",
        "top_3_risks": summary.get("top_3_risks") or [],
        "top_3_wins": summary.get("top_3_wins") or [],
        "margin_recommendations": summary.get("margin_recommendations") or [],
        "stats": result.get("stats") or {},
        "generated_at": result.get("generated_at"),
    }


def build_evidence(period_insights: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "granularity": period_insights.get("granularity"),
        "selected": period_insights.get("selected"),
        "current": period_insights.get("current"),
        "delta": period_insights.get("delta"),
        "signals": period_insights.get("signals") or [],
        "stats": stats,
    }


def persist_insight(
    *,
    granularity: str,
    period_start: str,
    period_end: str,
    result: dict[str, Any],
    evidence: dict[str, Any],
    provider: str | None,
    model: str | None,
) -> bool:
    try:
        upsert_insight(
            source=SOURCE,
            scope_key=scope_key(granularity, period_start),
            as_of=period_end,
            payload=build_payload(result),
            evidence=evidence,
            provider=provider,
            model=model,
            status="ok",
        )
        return True
    except Exception:  # noqa: BLE001 — storage must not fail the HTTP response
        logger.warning(
            "operation=iworker_insights status=store_failed granularity=%s period_start=%s",
            granularity,
            period_start,
            exc_info=True,
        )
        return False


def get_stored_insight(
    granularity: str,
    period_start: str,
    period_end: str,
) -> dict[str, Any] | None:
    return get_insight(SOURCE, scope_key(granularity, period_start), period_end)


def response_from_row(row: dict[str, Any] | None, *, period_label: str | None = None) -> dict[str, Any]:
    payload = (row or {}).get("payload") or {}
    stats = dict(payload.get("stats") or {})
    if period_label and not stats.get("period_label"):
        stats["period_label"] = period_label
    generated_at = payload.get("generated_at") or (row or {}).get("generated_at")
    return {
        "status": "success" if row and row.get("status") == "ok" else "empty",
        "generated_at": generated_at,
        "provider": (row or {}).get("provider"),
        "model": (row or {}).get("model"),
        "contractor": "iWorker Contractor",
        "source_data": "Live Google Sheets Ingestion",
        "summary": {
            "leadership_brief_text": payload.get("brief") or "",
            "top_3_risks": payload.get("top_3_risks") or [],
            "top_3_wins": payload.get("top_3_wins") or [],
            "margin_recommendations": payload.get("margin_recommendations") or [],
        },
        "stats": stats,
        "stored": row is not None,
    }
