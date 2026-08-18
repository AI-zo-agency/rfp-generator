"""Persist and query LLM call cost/token logs (SQLite or Supabase)."""

from __future__ import annotations

import logging
import sqlite3
import time
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
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_run_id ON llm_call_log (run_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_rfp_id ON llm_call_log (rfp_id);
"""

# Columns added after the table shipped. CREATE TABLE IF NOT EXISTS will not add
# them to a database created by an earlier build, so they are applied separately.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cache_creation_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_read_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
)


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
        _migrate_sqlite_columns(conn)


def _migrate_sqlite_columns(conn: sqlite3.Connection) -> None:
    """Add post-ship columns to an existing table. Idempotent."""
    existing = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(llm_call_log)").fetchall()
    }
    for name, ddl in _ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE llm_call_log ADD COLUMN {name} {ddl}")


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
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
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
            "cache_creation_input_tokens": max(0, int(cache_creation_input_tokens)),
            "cache_read_input_tokens": max(0, int(cache_read_input_tokens)),
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
                input_tokens, output_tokens, cache_creation_input_tokens,
                cache_read_input_tokens, cost_usd, latency_ms,
                tokens_estimated, created_at
            ) VALUES (
                :id, :run_id, :rfp_id, :node_name, :model, :tier, :provider,
                :input_tokens, :output_tokens, :cache_creation_input_tokens,
                :cache_read_input_tokens, :cost_usd, :latency_ms,
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
    try:
        client.table("llm_call_log").insert(payload).execute()
        return
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        logger.warning("llm_call_log supabase insert failed: %s", message[:240])
        # Backward-compatible fallback for older Supabase schema caches/tables
        # that do not yet have cache token columns.
        if (
            "cache_creation_input_tokens" not in message
            and "cache_read_input_tokens" not in message
            and "PGRST204" not in message
            and "Could not find" not in message
        ):
            raise

    legacy_payload = {
        k: v
        for k, v in payload.items()
        if k not in {"cache_creation_input_tokens", "cache_read_input_tokens"}
    }
    client.table("llm_call_log").insert(legacy_payload).execute()


def get_rfp_cost_breakdown(rfp_id: str) -> dict[str, Any]:
    """Return total + per-node cost/token breakdown for one RFP (all runs)."""
    fid = (rfp_id or "").strip()
    if not fid:
        return {
            "rfp_id": "",
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "call_count": 0,
            "run_count": 0,
            "by_node": [],
            "by_run": [],
            "by_run_detailed": [],
        }
    try:
        ensure_llm_call_log_table()
        ids = _rfp_id_aliases(fid)
        rows: list[dict[str, Any]] = []
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                if _use_supabase():
                    rows = _fetch_supabase_by_rfp(ids)
                else:
                    rows = _fetch_sqlite_by_rfp(ids)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "get_rfp_cost_breakdown attempt %s/3 failed: %s",
                    attempt + 1,
                    str(exc)[:240],
                )
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
        if last_exc is not None:
            return {
                "rfp_id": fid,
                "total_cost_usd": 0.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "call_count": 0,
                "run_count": 0,
                "by_node": [],
                "by_run": [],
                "by_run_detailed": [],
                "error": str(last_exc)[:200],
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_rfp_cost_breakdown failed: %s", str(exc)[:240])
        return {
            "rfp_id": fid,
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "call_count": 0,
            "run_count": 0,
            "by_node": [],
            "by_run": [],
            "by_run_detailed": [],
            "error": str(exc)[:200],
        }

    by_node: dict[str, dict[str, Any]] = {}
    by_run: dict[str, dict[str, Any]] = {}
    by_run_node: dict[str, dict[str, dict[str, Any]]] = {}
    total_cost = 0.0
    total_in = 0
    total_out = 0
    for row in rows:
        node = str(row.get("node_name") or "unknown")
        run = str(row.get("run_id") or "unknown")
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
            },
        )
        bucket["cost_usd"] += cost
        bucket["input_tokens"] += inp
        bucket["output_tokens"] += out
        bucket["calls"] += 1

        rb = by_run.setdefault(
            run,
            {
                "run_id": run,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
            },
        )
        rb["cost_usd"] += cost
        rb["input_tokens"] += inp
        rb["output_tokens"] += out
        rb["calls"] += 1

        rnodes = by_run_node.setdefault(run, {})
        rn = rnodes.setdefault(
            node,
            {
                "node_name": node,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
            },
        )
        rn["cost_usd"] += cost
        rn["input_tokens"] += inp
        rn["output_tokens"] += out
        rn["calls"] += 1

    nodes = sorted(by_node.values(), key=lambda b: b["cost_usd"], reverse=True)
    runs = sorted(by_run.values(), key=lambda b: b["cost_usd"], reverse=True)
    for bucket in nodes:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)
    for bucket in runs:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)

    run_details: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run.get("run_id") or "unknown")
        node_rows = list(by_run_node.get(run_id, {}).values())
        node_rows.sort(key=lambda b: float(b.get("cost_usd") or 0.0), reverse=True)
        for nr in node_rows:
            nr["cost_usd"] = round(float(nr.get("cost_usd") or 0.0), 6)
        primary_node = (
            str(node_rows[0].get("node_name") or "")
            if node_rows
            else "unknown"
        )
        run_type = "other"
        node_join = " ".join(str(n.get("node_name") or "") for n in node_rows[:6]).lower()
        if "section_chat" in node_join:
            run_type = "chat"
        elif "fulfill-scan" in node_join or "fulfill_scan" in node_join:
            run_type = "complete_scan"
        elif any(
            marker in node_join
            for marker in (
                # Phase 2 intelligence agent nodes
                "writing_briefs",
                "dynamic_section_planner",
                "closing_requirement",
                "rfp_budget",
                "stage35",
                "budget_grounding",
                "opportunity_extract",
                "strategy_delivery",
                "compliance_matrix",
                "risk_register",
                "evidence_alignment",
                "boilerplate_tailor",
                "style_pack",
                "outline_planner",
                "fetch_proposal_context",
                "case_select",
                "build_case_studies",
                # Sections 1–3/graph nodes
                "section_1",
                "team_",
                "bio_",
                "join_sections",
                "validate_sections_editorial",
            )
        ):
            run_type = "generate_proposal"
        elif (
            "phase-" in node_join
            or "pipeline" in node_join
            or "generate" in node_join
            or "sections-1-3" in node_join
        ):
            run_type = "generate_proposal"
        run_details.append(
            {
                **run,
                "run_type": run_type,
                "primary_node": primary_node or "unknown",
                "by_node": node_rows,
            }
        )

    return {
        "rfp_id": fid,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "call_count": len(rows),
        "run_count": len(by_run),
        "by_node": nodes,
        "by_run": runs,
        "by_run_detailed": run_details,
    }


def get_run_total_cost_usd(run_id: str) -> float:
    """Fast aggregate cost for a run_id (used by budget guards)."""
    rid = (run_id or "").strip()
    if not rid:
        return 0.0
    try:
        ensure_llm_call_log_table()
        if _use_supabase():
            rows = _fetch_supabase(rid)
            return round(
                float(sum(float(r.get("cost_usd") or 0.0) for r in rows)),
                6,
            )
        with _connect() as conn:
            cur = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_call_log WHERE run_id = ?",
                (rid,),
            )
            row = cur.fetchone()
            return round(float(row[0] or 0.0), 6) if row else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_run_total_cost_usd failed: %s", str(exc)[:240])
        return 0.0


def get_global_cost_summary() -> dict[str, Any]:
    """Aggregate LLM spend across all proposals and pipeline stages."""
    try:
        ensure_llm_call_log_table()
        if _use_supabase():
            rows = _fetch_supabase_all()
        else:
            rows = _fetch_sqlite_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_global_cost_summary failed: %s", str(exc)[:240])
        return {
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "call_count": 0,
            "proposal_count": 0,
            "by_proposal": [],
            "by_node": [],
            "by_model": [],
            "unattributed_cost_usd": 0.0,
            "unknown_node_cost_usd": 0.0,
            "unknown_node_calls": 0,
            "unknown_breakdown": {"by_model": [], "by_date": []},
            "error": str(exc)[:200],
        }

    by_rfp: dict[str, dict[str, Any]] = {}
    by_node: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    unknown_by_model: dict[str, dict[str, Any]] = {}
    unknown_by_date: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    total_in = 0
    total_out = 0

    for row in rows:
        rfp = str(row.get("rfp_id") or "").strip() or "unknown"
        node = str(row.get("node_name") or "").strip() or "unknown"
        model = str(row.get("model") or "unknown")
        cost = float(row.get("cost_usd") or 0)
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        created = str(row.get("created_at") or "")[:10] or "unknown"
        total_cost += cost
        total_in += inp
        total_out += out

        if node == "unknown":
            umb = unknown_by_model.setdefault(
                model,
                {"model": model, "cost_usd": 0.0, "calls": 0},
            )
            umb["cost_usd"] += cost
            umb["calls"] += 1
            udb = unknown_by_date.setdefault(
                created,
                {"date": created, "cost_usd": 0.0, "calls": 0},
            )
            udb["cost_usd"] += cost
            udb["calls"] += 1

        fb = by_rfp.setdefault(
            rfp,
            {
                "rfp_id": rfp,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
                "runs": set(),
            },
        )
        fb["cost_usd"] += cost
        fb["input_tokens"] += inp
        fb["output_tokens"] += out
        fb["calls"] += 1
        fb["runs"].add(str(row.get("run_id") or "unknown"))

        nb = by_node.setdefault(
            node,
            {"node_name": node, "cost_usd": 0.0, "calls": 0},
        )
        nb["cost_usd"] += cost
        nb["calls"] += 1

        mb = by_model.setdefault(
            model,
            {"model": model, "cost_usd": 0.0, "calls": 0},
        )
        mb["cost_usd"] += cost
        mb["calls"] += 1

    proposals = []
    for rfp, d in by_rfp.items():
        proposals.append(
            {
                "rfp_id": rfp,
                "cost_usd": round(d["cost_usd"], 6),
                "input_tokens": d["input_tokens"],
                "output_tokens": d["output_tokens"],
                "calls": d["calls"],
                "run_count": len(d["runs"]),
            }
        )
    proposals.sort(key=lambda p: p["cost_usd"], reverse=True)

    nodes = sorted(by_node.values(), key=lambda b: b["cost_usd"], reverse=True)
    models = sorted(by_model.values(), key=lambda b: b["cost_usd"], reverse=True)
    for bucket in nodes:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)
    for bucket in models:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)

    known_rfp_count = sum(1 for p in proposals if p["rfp_id"] != "unknown")

    unknown_node = by_node.get("unknown", {})
    unknown_models = sorted(unknown_by_model.values(), key=lambda b: b["cost_usd"], reverse=True)
    unknown_dates = sorted(unknown_by_date.values(), key=lambda b: b["date"], reverse=True)
    for bucket in unknown_models:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)
    for bucket in unknown_dates:
        bucket["cost_usd"] = round(float(bucket["cost_usd"]), 6)

    return {
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "call_count": len(rows),
        "proposal_count": known_rfp_count,
        "unattributed_cost_usd": round(
            float(by_rfp.get("unknown", {}).get("cost_usd", 0)),
            6,
        ),
        "unknown_node_cost_usd": round(float(unknown_node.get("cost_usd", 0)), 6),
        "unknown_node_calls": int(unknown_node.get("calls", 0)),
        "unknown_breakdown": {
            "by_model": unknown_models,
            "by_date": unknown_dates,
        },
        "by_proposal": proposals,
        "by_node": nodes,
        "by_model": models,
    }


def _fetch_sqlite_by_rfp(rfp_ids: list[str]) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        ids = [rid for rid in rfp_ids if rid]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"""
            SELECT run_id, node_name, model, cost_usd, input_tokens, output_tokens, created_at
            FROM llm_call_log
            WHERE rfp_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            tuple(ids),
        )
        return [dict(row) for row in cur.fetchall()]


def _fetch_supabase_by_rfp(rfp_ids: list[str]) -> list[dict[str, Any]]:
    from app.services import supabase_db as sb

    client = sb._get_client()  # noqa: SLF001
    rows: list[dict[str, Any]] = []
    offset = 0
    page = 1000
    while True:
        result = (
            client.table("llm_call_log")
            .select("run_id,node_name,model,cost_usd,input_tokens,output_tokens,created_at")
            .in_("rfp_id", rfp_ids)
            .order("created_at")
            .range(offset, offset + page - 1)
            .execute()
        )
        batch = [dict(r) for r in (result.data or []) if isinstance(r, dict)]
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def _rfp_id_aliases(rfp_id: str) -> list[str]:
    """Variants that may appear in instrumentation rows for one RFP."""
    rid = (rfp_id or "").strip()
    if not rid:
        return [""]
    aliases = {rid}
    try:
        from app.services.rfp_repository import get_rfp

        rec = get_rfp(rid)
        if rec:
            if rec.id:
                aliases.add(str(rec.id).strip())
            if rec.external_id:
                aliases.add(str(rec.external_id).strip())
                aliases.add(f"rfp-jw-{str(rec.external_id).strip()}")
    except Exception:  # noqa: BLE001
        pass
    if rid.startswith("rfp-jw-"):
        aliases.add(rid.replace("rfp-jw-", "", 1))
    return [a for a in aliases if a]


def _fetch_sqlite_all() -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT run_id, rfp_id, node_name, model, cost_usd, input_tokens, output_tokens, created_at
            FROM llm_call_log ORDER BY created_at ASC
            """
        )
        return [dict(row) for row in cur.fetchall()]


def _fetch_supabase_all() -> list[dict[str, Any]]:
    from app.services import supabase_db as sb

    client = sb._get_client()  # noqa: SLF001
    rows: list[dict[str, Any]] = []
    offset = 0
    page = 1000
    while True:
        result = (
            client.table("llm_call_log")
            .select(
                "run_id,rfp_id,node_name,model,cost_usd,input_tokens,output_tokens,created_at"
            )
            .order("created_at")
            .range(offset, offset + page - 1)
            .execute()
        )
        batch = [dict(r) for r in (result.data or []) if isinstance(r, dict)]
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


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
    total_cache_write = 0
    total_cache_read = 0
    for row in rows:
        node = str(row.get("node_name") or "unknown")
        cost = float(row.get("cost_usd") or 0)
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        cache_write = int(row.get("cache_creation_input_tokens") or 0)
        cache_read = int(row.get("cache_read_input_tokens") or 0)
        total_cost += cost
        total_in += inp
        total_out += out
        total_cache_write += cache_write
        total_cache_read += cache_read
        bucket = by_node.setdefault(
            node,
            {
                "node_name": node,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "calls": 0,
                "latency_ms": 0,
            },
        )
        bucket["cost_usd"] += cost
        bucket["input_tokens"] += inp
        bucket["output_tokens"] += out
        bucket["cache_creation_input_tokens"] += cache_write
        bucket["cache_read_input_tokens"] += cache_read
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
        "total_cache_creation_input_tokens": total_cache_write,
        "total_cache_read_input_tokens": total_cache_read,
        "call_count": len(rows),
        "by_node": nodes,
    }


def _fetch_sqlite(run_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT node_name, model, tier, provider, input_tokens, output_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens,
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
            "cache_creation_input_tokens,cache_read_input_tokens,"
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
        f"tokens_out={breakdown.get('total_output_tokens', 0)} "
        f"cache_write={breakdown.get('total_cache_creation_input_tokens', 0)} "
        f"cache_read={breakdown.get('total_cache_read_input_tokens', 0)}",
    ]
    for node in breakdown.get("by_node") or []:
        lines.append(
            f"  - {node.get('node_name')}: "
            f"${float(node.get('cost_usd') or 0):.4f} "
            f"({node.get('calls')} calls, "
            f"in={node.get('input_tokens')} out={node.get('output_tokens')})"
        )
    return "\n".join(lines)
