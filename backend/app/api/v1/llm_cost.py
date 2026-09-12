"""LLM usage cost endpoints — per-proposal and global summaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.llm_call_log import get_global_cost_summary, get_rfp_cost_breakdown
from app.services.rfp_repository import get_rfp, get_rfps_by_ids

router = APIRouter(prefix="/llm-cost", tags=["llm-cost"])


def _attach_titles(summary: dict[str, Any]) -> dict[str, Any]:
    """Join RFP titles onto per-proposal rows for the dashboard."""
    by_proposal = summary.get("by_proposal") or []
    ids = [str(row.get("rfp_id") or "") for row in by_proposal]
    ids = [rid for rid in ids if rid and rid != "unknown"]
    try:
        by_id = get_rfps_by_ids(ids)
    except Exception:  # noqa: BLE001
        by_id = {}
    enriched: list[dict[str, Any]] = []
    for row in by_proposal:
        rfp_id = str(row.get("rfp_id") or "")
        rec = by_id.get(rfp_id)
        title = rec.title if rec and rec.title else ""
        enriched.append({**row, "title": title})
    return {**summary, "by_proposal": enriched}


@router.get("/summary")
def llm_cost_summary() -> dict[str, Any]:
    """Total LLM spend, per-proposal and per-pipeline-stage breakdown."""
    return _attach_titles(get_global_cost_summary())


@router.get("/monthly-budget")
def llm_monthly_budget() -> dict[str, Any]:
    """Lightweight org-wide monthly spend vs hard cap (navbar / guards)."""
    from app.services.monthly_llm_budget import get_monthly_budget_status

    return get_monthly_budget_status()


@router.get("/rfps/{rfp_id}")
def llm_cost_for_rfp(rfp_id: str) -> dict[str, Any]:
    """LLM spend for one RFP across all generate / scan / chat runs."""
    breakdown = get_rfp_cost_breakdown(rfp_id)
    title = ""
    try:
        rec = get_rfp(rfp_id)
        if rec and rec.title:
            title = rec.title
    except Exception:  # noqa: BLE001
        title = ""
    return {**breakdown, "title": title}
