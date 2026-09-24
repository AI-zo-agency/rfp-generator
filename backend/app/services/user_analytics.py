"""Product analytics events (pageviews, clicks, dwell) — separate from audit.

USER_ANALYTICS_STORE=auto|sqlite|supabase (mirrors user activity store).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from app.services.llm_call_context import get_llm_user_email, normalize_user_email

logger = logging.getLogger(__name__)

Workspace = Literal["rfp", "financial", "leads"]
EventType = Literal["page_view", "tab_view", "ui_click", "heartbeat", "funnel_step"]

WORKSPACES = frozenset({"rfp", "financial", "leads"})
EVENT_TYPES = frozenset(
    {"page_view", "tab_view", "ui_click", "heartbeat", "funnel_step"}
)
MAX_BATCH = 50

# Named funnels: ordered feature / funnel_step ids
FUNNELS: dict[str, list[dict[str, str]]] = {
    "rfp": [
        {
            "id": "rfp_to_export",
            "label": "RFP → Proposal → Export",
            "steps": "rfp.open,proposal.open,proposal.build,proposal.exported",
        },
        {
            "id": "gonogo_to_go",
            "label": "Go/No-Go → Mark Go",
            "steps": "rfp.gonogo_run,rfp.mark_go",
        },
        {
            "id": "personas_to_build",
            "label": "Key personas → Build",
            "steps": "proposal.personas_save,proposal.build",
        },
        {
            "id": "build_to_scan",
            "label": "Build → Review & fix",
            "steps": "proposal.build,proposal.review_fix",
        },
    ],
    "financial": [
        {
            "id": "financial_ai",
            "label": "Enter → Tab → AI open → Chat",
            "steps": "financial.enter,financial.tab,financial.ai_open,financial.ai_chat",
        },
    ],
    "leads": [
        {
            "id": "lead_prep",
            "label": "Open → Enrich → Prep → Ready",
            "steps": "lead.open,lead.enrich,lead.prep_generate,lead.outreach_ready",
        },
    ],
}

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS user_analytics_events (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  workspace     TEXT NOT NULL,
  actor_email   TEXT NOT NULL DEFAULT 'system',
  session_id    TEXT NOT NULL DEFAULT '',
  event_type    TEXT NOT NULL,
  path          TEXT,
  tab           TEXT,
  view          TEXT,
  feature       TEXT,
  entity_type   TEXT,
  entity_id     TEXT,
  duration_ms   INTEGER NOT NULL DEFAULT 0,
  engaged       INTEGER NOT NULL DEFAULT 0,
  metadata      TEXT NOT NULL DEFAULT '{}',
  client_ts     TEXT
);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_time
  ON user_analytics_events (workspace, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_actor_time
  ON user_analytics_events (workspace, actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_type_time
  ON user_analytics_events (workspace, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_feature_time
  ON user_analytics_events (workspace, feature, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_session_time
  ON user_analytics_events (session_id, created_at DESC);
"""


def _store() -> Literal["sqlite", "supabase"]:
    from app.core.config import settings
    from app.services.supabase_db import use_supabase_db

    mode = (getattr(settings, "user_analytics_store", None) or "auto").strip().lower()
    if mode == "sqlite":
        return "sqlite"
    if mode == "supabase":
        return "supabase"
    return "supabase" if use_supabase_db() else "sqlite"


def ensure_analytics_table() -> None:
    if _store() != "sqlite":
        return
    from app.services.rfp_repository import _connect, init_db as init_rfp_db

    init_rfp_db()
    with _connect() as conn:
        conn.executescript(_SQLITE_DDL)


def ingest_events(
    *,
    workspace: str,
    events: list[dict[str, Any]],
    actor_email: str | None = None,
) -> dict[str, Any]:
    ws = (workspace or "").strip().lower()
    if ws not in WORKSPACES:
        return {"ok": False, "inserted": 0, "error": "invalid workspace"}
    if not events:
        return {"ok": True, "inserted": 0, "store": _store()}

    default_email = (
        normalize_user_email(actor_email) or get_llm_user_email() or "system"
    )
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for raw in events[:MAX_BATCH]:
        if not isinstance(raw, dict):
            continue
        et = str(raw.get("event_type") or "").strip().lower()
        if et not in EVENT_TYPES:
            continue
        email = normalize_user_email(raw.get("actor_email")) or default_email
        feature = (str(raw.get("feature") or "").strip()[:120]) or None
        if et in ("ui_click", "funnel_step") and not feature:
            continue
        duration = int(raw.get("duration_ms") or 0)
        if duration < 0:
            duration = 0
        if duration > 120_000:
            duration = 120_000
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        rows.append(
            {
                "id": str(uuid4()),
                "created_at": now,
                "workspace": ws,
                "actor_email": (email or "system")[:320],
                "session_id": str(raw.get("session_id") or "")[:80],
                "event_type": et,
                "path": (str(raw.get("path") or "").strip()[:300]) or None,
                "tab": (str(raw.get("tab") or "").strip()[:80]) or None,
                "view": (str(raw.get("view") or "").strip()[:80]) or None,
                "feature": feature,
                "entity_type": (str(raw.get("entity_type") or "").strip()[:80]) or None,
                "entity_id": (str(raw.get("entity_id") or "").strip()[:200]) or None,
                "duration_ms": duration,
                "engaged": bool(raw.get("engaged")),
                "metadata": meta,
                "client_ts": str(raw.get("client_ts") or "")[:64] or None,
            }
        )

    if not rows:
        return {"ok": True, "inserted": 0, "store": _store()}

    try:
        store = _store()
        if store == "sqlite":
            ensure_analytics_table()
            _insert_sqlite_batch(rows)
        else:
            from app.services.supabase_db import (
                _get_client,
                _with_transient_retry,
                use_supabase_db,
            )

            if not use_supabase_db():
                return {"ok": False, "inserted": 0, "error": "Supabase not configured"}

            sb_rows = []
            for r in rows:
                sb = {k: v for k, v in r.items() if k != "created_at"}
                sb_rows.append(sb)

            def _ins() -> None:
                _get_client().table("user_analytics_events").insert(sb_rows).execute()

            _with_transient_retry("user_analytics.ingest", _ins)
        return {"ok": True, "inserted": len(rows), "store": store}
    except Exception as exc:  # noqa: BLE001
        logger.exception("user_analytics: ingest failed")
        return {"ok": False, "inserted": 0, "error": str(exc)}


def _insert_sqlite_batch(rows: list[dict[str, Any]]) -> None:
    from app.services.rfp_repository import _connect

    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO user_analytics_events (
              id, created_at, workspace, actor_email, session_id, event_type,
              path, tab, view, feature, entity_type, entity_id,
              duration_ms, engaged, metadata, client_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["id"],
                    r["created_at"],
                    r["workspace"],
                    r["actor_email"],
                    r["session_id"],
                    r["event_type"],
                    r.get("path"),
                    r.get("tab"),
                    r.get("view"),
                    r.get("feature"),
                    r.get("entity_type"),
                    r.get("entity_id"),
                    r["duration_ms"],
                    1 if r["engaged"] else 0,
                    json.dumps(r.get("metadata") or {}),
                    r.get("client_ts"),
                )
                for r in rows
            ],
        )


def summarize(
    *,
    workspace: str,
    from_ts: str | None = None,
    to_ts: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    ws = (workspace or "").strip().lower()
    if ws not in WORKSPACES:
        return {"workspace": ws, "error": "invalid workspace"}

    now = datetime.now(timezone.utc)
    if not from_ts:
        from_ts = (now - timedelta(days=14)).isoformat()
    if not to_ts:
        to_ts = now.isoformat()
    actor_email = normalize_user_email(actor) or None

    try:
        rows = _fetch_range(ws, from_ts, to_ts, actor_email)
    except Exception as exc:  # noqa: BLE001
        logger.exception("user_analytics: summary fetch failed")
        return {
            "workspace": ws,
            "from": from_ts,
            "to": to_ts,
            "error": str(exc),
            "time": {"visible_ms": 0, "engaged_ms": 0},
            "top_pages": [],
            "top_features": [],
            "by_user": [],
            "funnels": [],
            "store": _store(),
        }

    visible_ms = 0
    engaged_ms = 0
    page_counts: dict[str, int] = defaultdict(int)
    feature_clicks: dict[str, int] = defaultdict(int)
    feature_users: dict[str, set[str]] = defaultdict(set)
    by_user: dict[str, dict[str, Any]] = {}

    def _user(email: str) -> dict[str, Any]:
        if email not in by_user:
            by_user[email] = {
                "actor_email": email,
                "visible_ms": 0,
                "engaged_ms": 0,
                "page_views": 0,
                "clicks": 0,
            }
        return by_user[email]

    users_by_feature: dict[str, set[str]] = defaultdict(set)

    def _is_self_uat_row(r: dict[str, Any]) -> bool:
        path = str(r.get("path") or "").split("?")[0].rstrip("/") or "/"
        tab = str(r.get("tab") or "").strip().lower()
        view = str(r.get("view") or "").strip().lower()
        if path == "/activity":
            return True
        if tab == "activity":
            return True
        if view in ("activity", "analytics", "audit"):
            return True
        return False

    for r in rows:
        if _is_self_uat_row(r):
            continue
        email = str(r.get("actor_email") or "system")
        et = str(r.get("event_type") or "")
        u = _user(email)
        if et == "heartbeat":
            dur = int(r.get("duration_ms") or 0)
            visible_ms += dur
            u["visible_ms"] += dur
            engaged = r.get("engaged")
            if engaged in (True, 1, "1", "true"):
                engaged_ms += dur
                u["engaged_ms"] += dur
        elif et in ("page_view", "tab_view"):
            key = str(r.get("path") or r.get("tab") or "unknown")
            if r.get("tab"):
                key = f"{r.get('path') or ''}#{r.get('tab')}"
            page_counts[key] += 1
            u["page_views"] += 1
        elif et in ("ui_click", "funnel_step"):
            feat = str(r.get("feature") or "").strip()
            if feat:
                feature_clicks[feat] += 1
                feature_users[feat].add(email)
                users_by_feature[feat].add(email)
                u["clicks"] += 1

    top_pages = sorted(
        [{"path": k, "views": v} for k, v in page_counts.items()],
        key=lambda x: -x["views"],
    )[:15]
    top_features = sorted(
        [
            {
                "feature": k,
                "clicks": v,
                "unique_users": len(feature_users[k]),
            }
            for k, v in feature_clicks.items()
        ],
        key=lambda x: -x["clicks"],
    )[:20]

    funnels_out = []
    for spec in FUNNELS.get(ws, []):
        steps = [s.strip() for s in spec["steps"].split(",") if s.strip()]
        step_rows = []
        prev_users: set[str] | None = None
        for step in steps:
            users = users_by_feature.get(step, set())
            if prev_users is not None:
                users = users & prev_users
            step_rows.append({"id": step, "users": len(users)})
            prev_users = users
        funnels_out.append(
            {"id": spec["id"], "label": spec["label"], "steps": step_rows}
        )

    return {
        "workspace": ws,
        "from": from_ts,
        "to": to_ts,
        "store": _store(),
        "time": {"visible_ms": visible_ms, "engaged_ms": engaged_ms},
        "top_pages": top_pages,
        "top_features": top_features,
        "by_user": sorted(by_user.values(), key=lambda x: -x["visible_ms"]),
        "funnels": funnels_out,
    }


def _fetch_range(
    workspace: str,
    from_ts: str,
    to_ts: str,
    actor_email: str | None,
) -> list[dict[str, Any]]:
    store = _store()
    if store == "sqlite":
        ensure_analytics_table()
        from app.services.rfp_repository import _connect

        clauses = ["workspace = ?", "created_at >= ?", "created_at <= ?"]
        params: list[Any] = [workspace, from_ts, to_ts]
        if actor_email:
            clauses.append("actor_email = ?")
            params.append(actor_email)
        sql = (
            "SELECT * FROM user_analytics_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at ASC LIMIT 20000"
        )
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    from app.services.supabase_db import _get_client, _with_transient_retry, use_supabase_db

    if not use_supabase_db():
        return []

    def _q() -> list[dict[str, Any]]:
        q = (
            _get_client()
            .table("user_analytics_events")
            .select(
                "id,created_at,workspace,actor_email,session_id,event_type,"
                "path,tab,view,feature,entity_type,entity_id,duration_ms,engaged,metadata"
            )
            .eq("workspace", workspace)
            .gte("created_at", from_ts)
            .lte("created_at", to_ts)
            .order("created_at", desc=False)
            .limit(20000)
        )
        if actor_email:
            q = q.eq("actor_email", actor_email)
        res = q.execute()
        return list(res.data or [])

    return _with_transient_retry("user_analytics.summary", _q)
