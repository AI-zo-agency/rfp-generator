"""First-party user activity events (RFP / Financial / Leads workspaces).

Emit from successful mutation handlers only. Failures never raise to callers —
activity logging must not break product flows.

Storage:
  USER_ACTIVITY_STORE=auto     → Supabase when configured, else SQLite (default)
  USER_ACTIVITY_STORE=sqlite   → always local SQLite (good for testing before
                                  applying the Supabase migration)
  USER_ACTIVITY_STORE=supabase → always Supabase (errors if not configured)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from app.services.llm_call_context import get_llm_user_email, normalize_user_email

logger = logging.getLogger(__name__)

Workspace = Literal["rfp", "financial", "leads"]
Outcome = Literal["recorded", "started", "completed", "failed", "cancelled"]
WORKSPACES: frozenset[str] = frozenset({"rfp", "financial", "leads"})
OUTCOMES: frozenset[str] = frozenset(
    {"recorded", "started", "completed", "failed", "cancelled"}
)

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS user_activity_events (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  workspace     TEXT NOT NULL,
  actor_email   TEXT NOT NULL DEFAULT 'system',
  action        TEXT NOT NULL,
  outcome       TEXT NOT NULL DEFAULT 'recorded',
  entity_type   TEXT,
  entity_id     TEXT,
  entity_label  TEXT,
  summary       TEXT NOT NULL,
  metadata      TEXT NOT NULL DEFAULT '{}',
  request_id    TEXT,
  run_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_uae_workspace_time
  ON user_activity_events (workspace, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uae_actor_time
  ON user_activity_events (actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uae_workspace_actor_time
  ON user_activity_events (workspace, actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uae_entity
  ON user_activity_events (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_uae_action
  ON user_activity_events (workspace, action);
"""


def _activity_store() -> Literal["sqlite", "supabase"]:
    from app.core.config import settings
    from app.services.supabase_db import use_supabase_db

    mode = (getattr(settings, "user_activity_store", None) or "auto").strip().lower()
    if mode == "sqlite":
        return "sqlite"
    if mode == "supabase":
        return "supabase"
    # auto
    return "supabase" if use_supabase_db() else "sqlite"


def ensure_user_activity_table() -> None:
    """Create SQLite table when using local store. No-op for Supabase."""
    if _activity_store() != "sqlite":
        return
    from app.services.rfp_repository import _connect, init_db as init_rfp_db

    init_rfp_db()
    with _connect() as conn:
        conn.executescript(_SQLITE_DDL)
        _migrate_sqlite_columns(conn)


def _migrate_sqlite_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(user_activity_events)").fetchall()
    }
    if "outcome" not in existing:
        conn.execute(
            "ALTER TABLE user_activity_events "
            "ADD COLUMN outcome TEXT NOT NULL DEFAULT 'recorded'"
        )


def emit_activity(
    *,
    workspace: Workspace | str,
    action: str,
    summary: str,
    actor_email: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_label: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    outcome: Outcome | str = "recorded",
) -> str | None:
    """Insert one activity row. Returns event id, or None on skip/failure."""
    ws = (workspace or "").strip().lower()
    if ws not in WORKSPACES:
        logger.warning("user_activity: invalid workspace %r", workspace)
        return None
    act = (action or "").strip()
    summ = (summary or "").strip()
    if not act or not summ:
        logger.warning("user_activity: missing action/summary")
        return None

    oc = (outcome or "recorded").strip().lower()
    if oc not in OUTCOMES:
        oc = "recorded"

    email = normalize_user_email(actor_email) or get_llm_user_email() or "system"
    event_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": event_id,
        "created_at": now,
        "workspace": ws,
        "actor_email": email[:320],
        "action": act[:200],
        "outcome": oc,
        "entity_type": (entity_type or "").strip()[:80] or None,
        "entity_id": (entity_id or "").strip()[:200] or None,
        "entity_label": (entity_label or "").strip()[:300] or None,
        "summary": summ[:500],
        "metadata": metadata or {},
        "request_id": (request_id or "").strip()[:120] or None,
        "run_id": (run_id or "").strip()[:120] or None,
    }
    try:
        store = _activity_store()
        if store == "sqlite":
            ensure_user_activity_table()
            _insert_sqlite(row)
            return event_id

        from app.services.supabase_db import _get_client, _with_transient_retry, use_supabase_db

        if not use_supabase_db():
            logger.warning("user_activity: supabase store requested but not configured")
            return None

        # Postgres supplies created_at DEFAULT; omit client clock if present
        sb_row = {k: v for k, v in row.items() if k != "created_at"}

        def _insert() -> None:
            _get_client().table("user_activity_events").insert(sb_row).execute()

        _with_transient_retry("user_activity.insert", _insert)
        return event_id
    except Exception:  # noqa: BLE001 — never break callers
        logger.exception("user_activity: failed to emit %s/%s", ws, act)
        return None


def list_activity(
    *,
    workspace: Workspace | str,
    limit: int = 50,
    cursor: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    entity_id: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> dict[str, Any]:
    """Paginated activity feed for one workspace (newest first)."""
    ws = (workspace or "").strip().lower()
    if ws not in WORKSPACES:
        return {"workspace": ws, "items": [], "next_cursor": None, "stats": _empty_stats()}

    limit = max(1, min(int(limit or 50), 100))
    try:
        store = _activity_store()
        if store == "sqlite":
            ensure_user_activity_table()
            rows = _list_sqlite(
                workspace=ws,
                limit=limit + 1,
                cursor=cursor,
                actor=actor,
                action=action,
                entity_id=entity_id,
                from_ts=from_ts,
                to_ts=to_ts,
            )
            has_more = len(rows) > limit
            page = rows[:limit]
            next_cursor = page[-1].get("created_at") if has_more and page else None
            return {
                "workspace": ws,
                "items": [_serialize(r) for r in page],
                "next_cursor": next_cursor,
                "stats": _stats_sqlite(ws),
                "store": "sqlite",
            }

        from app.services.supabase_db import _get_client, _with_transient_retry, use_supabase_db

        if not use_supabase_db():
            return {
                "workspace": ws,
                "items": [],
                "next_cursor": None,
                "stats": _empty_stats(),
                "error": "Supabase is not configured",
                "store": "supabase",
            }

        def _query() -> list[dict[str, Any]]:
            q = (
                _get_client()
                .table("user_activity_events")
                .select(
                    "id,created_at,workspace,actor_email,action,outcome,"
                    "entity_type,entity_id,entity_label,summary,metadata,request_id,run_id"
                )
                .eq("workspace", ws)
                .order("created_at", desc=True)
                .limit(limit + 1)
            )
            email = normalize_user_email(actor)
            if email:
                q = q.eq("actor_email", email)
            act = (action or "").strip()
            if act:
                q = q.eq("action", act)
            eid = (entity_id or "").strip()
            if eid:
                q = q.eq("entity_id", eid)
            if from_ts:
                q = q.gte("created_at", from_ts)
            if to_ts:
                q = q.lte("created_at", to_ts)
            if cursor:
                q = q.lt("created_at", cursor)
            res = q.execute()
            return list(res.data or [])

        rows = _with_transient_retry("user_activity.list", _query)
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = page[-1].get("created_at") if has_more and page else None
        return {
            "workspace": ws,
            "items": [_serialize(r) for r in page],
            "next_cursor": next_cursor,
            "stats": _stats_for_workspace_supabase(ws),
            "store": "supabase",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("user_activity: list failed for %s", ws)
        return {
            "workspace": ws,
            "items": [],
            "next_cursor": None,
            "stats": _empty_stats(),
            "error": str(exc),
        }


def _insert_sqlite(row: dict[str, Any]) -> None:
    from app.services.rfp_repository import _connect

    meta = row.get("metadata") or {}
    if not isinstance(meta, str):
        meta = json.dumps(meta)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_activity_events (
              id, created_at, workspace, actor_email, action, outcome,
              entity_type, entity_id, entity_label, summary, metadata,
              request_id, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["created_at"],
                row["workspace"],
                row["actor_email"],
                row["action"],
                row["outcome"],
                row.get("entity_type"),
                row.get("entity_id"),
                row.get("entity_label"),
                row["summary"],
                meta,
                row.get("request_id"),
                row.get("run_id"),
            ),
        )


def _list_sqlite(
    *,
    workspace: str,
    limit: int,
    cursor: str | None,
    actor: str | None,
    action: str | None,
    entity_id: str | None,
    from_ts: str | None,
    to_ts: str | None,
) -> list[dict[str, Any]]:
    from app.services.rfp_repository import _connect

    clauses = ["workspace = ?"]
    params: list[Any] = [workspace]
    email = normalize_user_email(actor)
    if email:
        clauses.append("actor_email = ?")
        params.append(email)
    act = (action or "").strip()
    if act:
        clauses.append("action = ?")
        params.append(act)
    eid = (entity_id or "").strip()
    if eid:
        clauses.append("entity_id = ?")
        params.append(eid)
    if from_ts:
        clauses.append("created_at >= ?")
        params.append(from_ts)
    if to_ts:
        clauses.append("created_at <= ?")
        params.append(to_ts)
    if cursor:
        clauses.append("created_at < ?")
        params.append(cursor)
    params.append(limit)
    sql = (
        "SELECT * FROM user_activity_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC LIMIT ?"
    )
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        meta = d.get("metadata")
        if isinstance(meta, str):
            try:
                d["metadata"] = json.loads(meta)
            except json.JSONDecodeError:
                d["metadata"] = {}
        out.append(d)
    return out


def _stats_sqlite(workspace: str) -> dict[str, Any]:
    from app.services.rfp_repository import _connect

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_iso = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)).isoformat()
    try:
        with _connect() as conn:
            events_today = conn.execute(
                "SELECT COUNT(*) FROM user_activity_events "
                "WHERE workspace = ? AND created_at >= ?",
                (workspace, today_start),
            ).fetchone()[0]
            actors = conn.execute(
                "SELECT DISTINCT actor_email FROM user_activity_events "
                "WHERE workspace = ? AND created_at >= ? AND trim(actor_email) != ''",
                (workspace, week_iso),
            ).fetchall()
        return {
            "events_today": int(events_today or 0),
            "unique_actors_7d": len(actors),
        }
    except Exception:  # noqa: BLE001
        logger.exception("user_activity: sqlite stats failed for %s", workspace)
        return _empty_stats()


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": str(row.get("id") or ""),
        "created_at": str(row.get("created_at") or ""),
        "workspace": str(row.get("workspace") or ""),
        "actor_email": str(row.get("actor_email") or ""),
        "action": str(row.get("action") or ""),
        "outcome": str(row.get("outcome") or "recorded"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "entity_label": row.get("entity_label"),
        "summary": str(row.get("summary") or ""),
        "metadata": meta,
        "request_id": row.get("request_id"),
        "run_id": row.get("run_id"),
    }


def _empty_stats() -> dict[str, Any]:
    return {"events_today": 0, "unique_actors_7d": 0}


def _stats_for_workspace_supabase(workspace: str) -> dict[str, Any]:
    """Light header stats; best-effort."""
    try:
        from app.services.supabase_db import _get_client, _with_transient_retry

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_iso = (
            now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        ).isoformat()

        def _today() -> int:
            res = (
                _get_client()
                .table("user_activity_events")
                .select("id", count="exact")
                .eq("workspace", workspace)
                .gte("created_at", today_start)
                .limit(1)
                .execute()
            )
            return int(res.count or 0)

        def _actors() -> int:
            res = (
                _get_client()
                .table("user_activity_events")
                .select("actor_email")
                .eq("workspace", workspace)
                .gte("created_at", week_iso)
                .limit(500)
                .execute()
            )
            emails = {
                str(r.get("actor_email") or "").strip().lower()
                for r in (res.data or [])
                if str(r.get("actor_email") or "").strip()
            }
            return len(emails)

        return {
            "events_today": _with_transient_retry("user_activity.stats_today", _today),
            "unique_actors_7d": _with_transient_retry("user_activity.stats_actors", _actors),
        }
    except Exception:  # noqa: BLE001
        logger.exception("user_activity: stats failed for %s", workspace)
        return _empty_stats()
