"""Persistence for proposal_tweak_events (SQLite locally, Supabase in production).

Same store switch as user_activity (`USER_ACTIVITY_STORE`). Text columns are
capped at 200,000 bytes. Functions log and return None/False on failure
instead of raising: recording a tweak must never break a save.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services import user_activity

logger = logging.getLogger(__name__)

MAX_TEXT_BYTES = 200_000

_TEXT_COLUMNS = ("instruction", "before_text", "after_text")
_SUMMARY_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "rfp_id",
    "rfp_title",
    "section_id",
    "section_title",
    "actor_email",
    "source",
    "bucket",
    "kind",
    "has_instruction",
    "words_before",
    "words_after",
    "words_added",
    "words_removed",
    "before_title",
    "after_title",
    "metadata",
)
_ALL_COLUMNS = _SUMMARY_COLUMNS + _TEXT_COLUMNS
_UPDATABLE = {
    "after_text",
    "after_title",
    "words_after",
    "words_added",
    "words_removed",
    "updated_at",
}

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS proposal_tweak_events (
  id              TEXT PRIMARY KEY,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  rfp_id          TEXT NOT NULL,
  rfp_title       TEXT NOT NULL DEFAULT '',
  section_id      TEXT NOT NULL,
  section_title   TEXT NOT NULL DEFAULT '',
  actor_email     TEXT NOT NULL,
  source          TEXT NOT NULL,
  bucket          TEXT NOT NULL,
  kind            TEXT NOT NULL,
  instruction     TEXT NOT NULL DEFAULT '',
  has_instruction INTEGER NOT NULL DEFAULT 0,
  words_before    INTEGER NOT NULL DEFAULT 0,
  words_after     INTEGER NOT NULL DEFAULT 0,
  words_added     INTEGER NOT NULL DEFAULT 0,
  words_removed   INTEGER NOT NULL DEFAULT 0,
  before_title    TEXT NOT NULL DEFAULT '',
  after_title     TEXT NOT NULL DEFAULT '',
  before_text     TEXT NOT NULL DEFAULT '',
  after_text      TEXT NOT NULL DEFAULT '',
  metadata        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pte_rfp_time ON proposal_tweak_events (rfp_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pte_actor_time ON proposal_tweak_events (actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pte_burst
  ON proposal_tweak_events (rfp_id, section_id, actor_email, updated_at DESC);
"""


def cap_text(text: str | None) -> str:
    """Truncate to MAX_TEXT_BYTES of UTF-8 without splitting a character."""
    value = text or ""
    data = value.encode("utf-8")
    if len(data) <= MAX_TEXT_BYTES:
        return value
    return data[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")


def _use_sqlite() -> bool:
    return user_activity._activity_store() == "sqlite"


def ensure_tweak_table() -> None:
    """Create the SQLite table when using the local store. No-op for Supabase."""
    if not _use_sqlite():
        return
    from app.services.rfp_repository import _connect, init_db as init_rfp_db

    init_rfp_db()
    with _connect() as conn:
        conn.executescript(_SQLITE_DDL)


def _prepare(record: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    created = str(record.get("created_at") or now)
    row: dict[str, Any] = {
        "id": str(uuid4()),
        "created_at": created,
        "updated_at": str(record.get("updated_at") or created),
        "rfp_id": str(record.get("rfp_id") or "").strip()[:200],
        "rfp_title": str(record.get("rfp_title") or "")[:300],
        "section_id": str(record.get("section_id") or "").strip()[:200],
        "section_title": str(record.get("section_title") or "")[:300],
        "actor_email": str(record.get("actor_email") or "system").strip().lower()[:320],
        "source": str(record.get("source") or "other"),
        "bucket": str(record.get("bucket") or "other"),
        "kind": str(record.get("kind") or "text"),
        "words_before": int(record.get("words_before") or 0),
        "words_after": int(record.get("words_after") or 0),
        "words_added": int(record.get("words_added") or 0),
        "words_removed": int(record.get("words_removed") or 0),
        "before_title": str(record.get("before_title") or "")[:300],
        "after_title": str(record.get("after_title") or "")[:300],
        "metadata": record.get("metadata") or {},
    }
    for column in _TEXT_COLUMNS:
        row[column] = cap_text(record.get(column))
    row["has_instruction"] = bool(row["instruction"].strip())
    return row


def _from_sqlite(raw: sqlite3.Row, columns: tuple[str, ...]) -> dict[str, Any]:
    out = {column: raw[column] for column in columns}
    out["has_instruction"] = bool(out.get("has_instruction"))
    meta = out.get("metadata")
    if isinstance(meta, str):
        try:
            out["metadata"] = json.loads(meta)
        except json.JSONDecodeError:
            out["metadata"] = {}
    return out


def _normalise_supabase(raw: dict[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
    out = {column: raw.get(column) for column in columns}
    out["has_instruction"] = bool(out.get("has_instruction"))
    if not isinstance(out.get("metadata"), dict):
        out["metadata"] = {}
    return out


def insert_tweak(record: dict[str, Any]) -> str | None:
    """Insert one tweak row. Returns its id, or None on failure."""
    row = _prepare(record)
    try:
        if _use_sqlite():
            ensure_tweak_table()
            from app.services.rfp_repository import _connect

            values = dict(row)
            values["metadata"] = json.dumps(values["metadata"])
            values["has_instruction"] = int(values["has_instruction"])
            placeholders = ", ".join("?" for _ in _ALL_COLUMNS)
            with _connect() as conn:
                conn.execute(
                    f"INSERT INTO proposal_tweak_events ({', '.join(_ALL_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    [values[c] for c in _ALL_COLUMNS],
                )
            return row["id"]

        from app.services.supabase_db import _get_client, _with_transient_retry

        def _insert() -> None:
            _get_client().table("proposal_tweak_events").insert(row).execute()

        _with_transient_retry("proposal_tweaks.insert", _insert)
        return row["id"]
    except Exception:  # noqa: BLE001 — never break callers
        logger.exception("proposal_tweaks: failed to insert tweak for %s", row["rfp_id"])
        return None


def update_tweak(tweak_id: str, fields: dict[str, Any]) -> bool:
    """Update the 'after' side of a tweak (used to extend a manual-edit burst)."""
    values = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not values:
        return False
    if "after_text" in values:
        values["after_text"] = cap_text(values["after_text"])
    try:
        if _use_sqlite():
            ensure_tweak_table()
            from app.services.rfp_repository import _connect

            assignments = ", ".join(f"{column} = ?" for column in values)
            with _connect() as conn:
                cursor = conn.execute(
                    f"UPDATE proposal_tweak_events SET {assignments} WHERE id = ?",
                    [*values.values(), tweak_id],
                )
            return cursor.rowcount > 0

        from app.services.supabase_db import _get_client, _with_transient_retry

        def _update() -> list[dict[str, Any]]:
            res = (
                _get_client()
                .table("proposal_tweak_events")
                .update(values)
                .eq("id", tweak_id)
                .execute()
            )
            return list(res.data or [])

        return bool(_with_transient_retry("proposal_tweaks.update", _update))
    except Exception:  # noqa: BLE001
        logger.exception("proposal_tweaks: failed to update tweak %s", tweak_id)
        return False


def find_open_burst(
    rfp_id: str, section_id: str, actor_email: str, since_iso: str
) -> dict[str, Any] | None:
    """Newest manual_edit row for this RFP/section/person updated at or after `since_iso`."""
    email = (actor_email or "").strip().lower()
    try:
        if _use_sqlite():
            ensure_tweak_table()
            from app.services.rfp_repository import _connect

            with _connect() as conn:
                conn.row_factory = sqlite3.Row
                raw = conn.execute(
                    "SELECT * FROM proposal_tweak_events WHERE rfp_id = ? AND section_id = ? "
                    "AND actor_email = ? AND source = 'manual_edit' AND updated_at >= ? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (rfp_id, section_id, email, since_iso),
                ).fetchone()
            return _from_sqlite(raw, _ALL_COLUMNS) if raw else None

        from app.services.supabase_db import _get_client, _with_transient_retry

        def _query() -> list[dict[str, Any]]:
            res = (
                _get_client()
                .table("proposal_tweak_events")
                .select(",".join(_ALL_COLUMNS))
                .eq("rfp_id", rfp_id)
                .eq("section_id", section_id)
                .eq("actor_email", email)
                .eq("source", "manual_edit")
                .gte("updated_at", since_iso)
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            return list(res.data or [])

        rows = _with_transient_retry("proposal_tweaks.find_burst", _query)
        return _normalise_supabase(rows[0], _ALL_COLUMNS) if rows else None
    except Exception:  # noqa: BLE001
        logger.exception("proposal_tweaks: burst lookup failed for %s/%s", rfp_id, section_id)
        return None


def list_tweaks(
    *,
    rfp_id: str | None = None,
    actor_email: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Tweak rows newest first, WITHOUT before/after text or instruction."""
    limit = max(1, min(int(limit or 500), 5000))
    email = (actor_email or "").strip().lower()
    try:
        if _use_sqlite():
            ensure_tweak_table()
            from app.services.rfp_repository import _connect

            clauses: list[str] = []
            params: list[Any] = []
            if rfp_id:
                clauses.append("rfp_id = ?")
                params.append(rfp_id)
            if email:
                clauses.append("actor_email = ?")
                params.append(email)
            if from_ts:
                clauses.append("created_at >= ?")
                params.append(from_ts)
            if to_ts:
                clauses.append("created_at <= ?")
                params.append(to_ts)
            where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
            with _connect() as conn:
                conn.row_factory = sqlite3.Row
                raws = conn.execute(
                    f"SELECT {', '.join(_SUMMARY_COLUMNS)} FROM proposal_tweak_events "
                    f"{where}ORDER BY created_at DESC LIMIT ?",
                    [*params, limit],
                ).fetchall()
            return [_from_sqlite(r, _SUMMARY_COLUMNS) for r in raws]

        from app.services.supabase_db import _get_client, _with_transient_retry

        def _query() -> list[dict[str, Any]]:
            q = (
                _get_client()
                .table("proposal_tweak_events")
                .select(",".join(_SUMMARY_COLUMNS))
                .order("created_at", desc=True)
                .limit(limit)
            )
            if rfp_id:
                q = q.eq("rfp_id", rfp_id)
            if email:
                q = q.eq("actor_email", email)
            if from_ts:
                q = q.gte("created_at", from_ts)
            if to_ts:
                q = q.lte("created_at", to_ts)
            return list(q.execute().data or [])

        rows = _with_transient_retry("proposal_tweaks.list", _query)
        return [_normalise_supabase(r, _SUMMARY_COLUMNS) for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("proposal_tweaks: list failed")
        return []


def get_tweak(tweak_id: str) -> dict[str, Any] | None:
    """One tweak with its full before/after text and instruction."""
    try:
        if _use_sqlite():
            ensure_tweak_table()
            from app.services.rfp_repository import _connect

            with _connect() as conn:
                conn.row_factory = sqlite3.Row
                raw = conn.execute(
                    "SELECT * FROM proposal_tweak_events WHERE id = ?", (tweak_id,)
                ).fetchone()
            return _from_sqlite(raw, _ALL_COLUMNS) if raw else None

        from app.services.supabase_db import _get_client, _with_transient_retry

        def _query() -> list[dict[str, Any]]:
            res = (
                _get_client()
                .table("proposal_tweak_events")
                .select(",".join(_ALL_COLUMNS))
                .eq("id", tweak_id)
                .limit(1)
                .execute()
            )
            return list(res.data or [])

        rows = _with_transient_retry("proposal_tweaks.get", _query)
        return _normalise_supabase(rows[0], _ALL_COLUMNS) if rows else None
    except Exception:  # noqa: BLE001
        logger.exception("proposal_tweaks: get failed for %s", tweak_id)
        return None
