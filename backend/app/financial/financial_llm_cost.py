"""LLM spend for the financial workspace.

Separate from `llm_call_log` by design, not by accident. That table belongs to
proposals: its readers join RFP titles and its global summary sweeps every row,
so a QuickBooks chat turn logged there would show up as proposal spend. There
is no RFP behind a chat turn and no honest value to put in `rfp_id`.

What is shared is `estimate_cost_usd` — tokens against a price list is
arithmetic, and it belongs to no domain.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.llm_pricing import estimate_cost_usd
from app.services.supabase_db import _get_client

logger = logging.getLogger(__name__)

_TABLE = "financial_llm_calls"


def record_call(
    *,
    thread_id: str,
    turn_id: str,
    node_name: str,
    model: str,
    tier: str,
    provider: str,
    usage: dict[str, Any],
    latency_ms: int,
    cache_ttl_1h: bool = False,
) -> None:
    """Insert one row. Never raises — a lost cost row must not lose the answer.

    Swallowing the error does mean a turn whose write fails is invisible to the
    caps, so a run of failures degrades the budget guard to nothing. That is the
    right trade at one row: the alternative is failing a question the user
    already paid for.
    """
    try:
        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cost = estimate_cost_usd(
            model=model,
            input_tokens=inp,
            output_tokens=out,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
            cache_ttl_1h=cache_ttl_1h,
        )
        _get_client().table(_TABLE).insert(
            {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "node_name": node_name,
                "model": model,
                "tier": tier,
                "provider": provider,
                "input_tokens": max(0, inp),
                "output_tokens": max(0, out),
                "cache_creation_input_tokens": max(0, cache_write),
                "cache_read_input_tokens": max(0, cache_read),
                "cost_usd": cost,
                "latency_ms": max(0, int(latency_ms)),
                "tokens_estimated": bool(usage.get("estimated")),
            }
        ).execute()
        logger.info(
            "operation=financial_llm_call thread=%s turn=%s node=%s model=%s "
            "in=%d out=%d cost_usd=%.6f latency_ms=%d",
            thread_id, turn_id, node_name, model, inp, out, cost, latency_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("financial LLM cost record failed (non-fatal): %s", str(exc)[:240])


def _sum_cost(column: str, value: str) -> float:
    """Total USD across rows matching one key.

    Summed in Python rather than SQL because a thread is a handful of calls and
    PostgREST has no aggregate without a view.
    ponytail: fine to a few hundred rows per thread; make it an RPC if a thread
    ever grows past that.
    """
    try:
        result = (
            _get_client().table(_TABLE).select("cost_usd").eq(column, value).execute()
        )
        return float(sum(float(r.get("cost_usd") or 0) for r in (result.data or [])))
    except Exception as exc:  # noqa: BLE001
        # Reported as zero spend, which fails open: a caps check that cannot read
        # its own ledger lets the turn through rather than blocking on an outage.
        logger.warning("financial LLM cost read failed (non-fatal): %s", str(exc)[:240])
        return 0.0


def thread_total_usd(thread_id: str) -> float:
    return _sum_cost("thread_id", thread_id)


def turn_total_usd(turn_id: str) -> float:
    return _sum_cost("turn_id", turn_id)


def thread_breakdown(thread_id: str) -> dict[str, Any]:
    """What `GET .../chat/cost` serves."""
    try:
        result = (
            _get_client()
            .table(_TABLE)
            .select("node_name,model,provider,input_tokens,output_tokens,cost_usd,created_at")
            .eq("thread_id", thread_id)
            .order("created_at")
            .execute()
        )
        rows = result.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("financial LLM breakdown failed (non-fatal): %s", str(exc)[:240])
        rows = []
    return {
        "thread_id": thread_id,
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
        "calls": rows,
    }
