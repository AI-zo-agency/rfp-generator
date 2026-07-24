"""Persist and query LLM call cost/token logs (SQLite or Supabase)."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.rfp_repository import _connect, init_db as init_rfp_db

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS llm_call_log (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    rfp_id TEXT NOT NULL DEFAULT '',
    node_name TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_run_id ON llm_call_log (run_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_rfp_id ON llm_call_log (rfp_id);
"""


def _use_supabase() -> bool:
    from app.services import supabase_db as sb

    return sb.use_supabase_db()


def ensure_llm_call_log_table() -> None:
    """Create SQLite table when not on Supabase. No-op for Postgres (use migration)."""
    init_rfp_db()
    if _use_supabase():
        return
    with _connect() as conn:
        conn.executescript(_DDL)


def record_llm_call(
    *,
    run_id: str,
    rfp_id: str,
    node_name: str,
    model: str,
    tier: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    tokens_estimated: bool = False,
) -> None:
    """Insert one instrumentation row. Never raises — observability must not break generation."""
    try:
        ensure_llm_call_log_table()
        row = {
            "id": str(uuid.uuid4()),
            "run_id": (run_id or "").strip() or "unknown",
            "rfp_id": (rfp_id or "").strip(),
            "node_name": (node_name or "").strip() or "unknown",
            "model": (model or "").strip(),
            "tier": (tier or "").strip(),
            "provider": (provider or "").strip(),
            "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)),
            "cost_usd": float(cost_usd),
            "latency_ms": max(0, int(latency_ms)),
            "tokens_estimated": 1 if tokens_estimated else 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if _use_supabase():
            _insert_supabase(row)
        else:
            _insert_sqlite(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_call_log record failed (non-fatal): %s", str(exc)[:240])


def _insert_sqlite(row: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO llm_call_log (
                id, run_id, rfp_id, node_name, model, tier, provider,
                input_tokens, output_tokens, cost_usd, latency_ms,
                tokens_estimated, created_at
            ) VALUES (
                :id, :run_id, :rfp_id, :node_name, :model, :tier, :provider,
                :input_tokens, :output_tokens, :cost_usd, :latency_ms,
                :tokens_estimated, :created_at
            )
            """,
            row,
        )


def _insert_supabase(row: dict[str, Any]) -> None:
    from app.services import supabase_db as sb

    client = sb._get_client()  # noqa: SLF001 — shared client helper
    payload = {
        **row,
        "tokens_estimated": bool(row["tokens_estimated"]),
    }
    client.table("llm_call_log").insert(payload).execute()


def get_run_cost_breakdown(run_id: str) -> dict[str, Any]:
    """Return total + per-node cost/token breakdown for a proposal run."""
    rid = (run_id or "").strip()
    if not rid:
        return {
            "run_id": "",
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "call_count": 0,
            "by_node": [],
        }
    try:
        ensure_llm_call_log_table()
        if _use_supabase():
            rows = _fetch_supabase(rid)
        else:
            rows = _fetch_sqlite(rid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_run_cost_breakdown failed: %s", str(exc)[:240])
        return {
            "run_id": rid,
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "call_count": 0,
            "by_node": [],
            "error": str(exc)[:200],
        }

    by_node: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    total_in = 0
    total_out = 0
    for row in rows:
        node = str(row.get("node_name") or "unknown")
        cost = float(row.get("cost_usd") or 0)
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        total_cost += cost
        total_in += inp
        total_out += out
        bucket = by_node.setdefault(
            node,
            {
                "node_name": node,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
                "latency_ms": 0,
            },
        )
        bucket["cost_usd"] += cost
        bucket["input_tokens"] += inp
        bucket["output_tokens"] += out
        bucket["calls"] += 1
        bucket["latency_ms"] += int(row.get("latency_ms") or 0)

    nodes = sorted(by_node.values(), key=lambda b: b["cost_usd"], reverse=True)
    for bucket in nodes:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)

    return {
        "run_id": rid,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "call_count": len(rows),
        "by_node": nodes,
    }


def _fetch_sqlite(run_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT node_name, model, tier, provider, input_tokens, output_tokens,
                   cost_usd, latency_ms, tokens_estimated, created_at
            FROM llm_call_log
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def _fetch_supabase(run_id: str) -> list[dict[str, Any]]:
    from app.services import supabase_db as sb

    client = sb._get_client()  # noqa: SLF001
    result = (
        client.table("llm_call_log")
        .select(
            "node_name,model,tier,provider,input_tokens,output_tokens,"
            "cost_usd,latency_ms,tokens_estimated,created_at"
        )
        .eq("run_id", run_id)
        .order("created_at")
        .execute()
    )
    data = result.data or []
    return [dict(row) for row in data if isinstance(row, dict)]


def format_cost_breakdown_log(breakdown: dict[str, Any]) -> str:
    """Human-readable one-block summary for logger.info."""
    lines = [
        f"LLM cost summary run_id={breakdown.get('run_id')}",
        f"  total_usd=${breakdown.get('total_cost_usd', 0):.4f} "
        f"calls={breakdown.get('call_count', 0)} "
        f"tokens_in={breakdown.get('total_input_tokens', 0)} "
        f"tokens_out={breakdown.get('total_output_tokens', 0)}",
    ]
    for node in breakdown.get("by_node") or []:
        lines.append(
            f"  - {node.get('node_name')}: "
            f"${float(node.get('cost_usd') or 0):.4f} "
            f"({node.get('calls')} calls, "
            f"in={node.get('input_tokens')} out={node.get('output_tokens')})"
        )
    return "\n".join(lines)
