"""Agent prompts live in Supabase (agent_prompts) and are edited on the Prompts page.

Every prompt stays in code as the default: `get_prompt(key, DEFAULT)` returns the
Supabase body, or the default when Supabase is unconfigured, unreachable, or has no
row for that key, so an outage never stops a pipeline.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "agent_prompts"
SUMMARY_COLUMNS = "key,group_name,name,description,source,is_edited,updated_at,updated_by"
DETAIL_COLUMNS = f"{SUMMARY_COLUMNS},body,default_body"
MAX_BODY_CHARS = 100_000

# API and Celery workers are separate processes, so an edit reaches each one on its next
# check. A check reads one timestamp; bodies are re-read only after something changed.
CHECK_INTERVAL_S = 60

_bodies: dict[str, str] = {}
_version: str | None = None
_next_check = 0.0
_loaded = False
_lock = threading.Lock()


class PromptNotFound(Exception):
    pass


class PromptConflict(Exception):
    """The prompt changed after the editor loaded it."""


def enabled() -> bool:
    from app.services.supabase_db import use_supabase_db

    return use_supabase_db()


def _client():
    from app.services.supabase_db import _get_client

    return _get_client()


def _refresh() -> None:
    global _bodies, _version, _next_check, _loaded
    _next_check = time.monotonic() + CHECK_INTERVAL_S
    _loaded = True
    latest = _client().table(TABLE).select("updated_at").order("updated_at", desc=True).limit(1).execute().data
    version = latest[0]["updated_at"] if latest else None
    if version != _version:
        rows = _client().table(TABLE).select("key,body").execute().data or []
        _bodies = {r["key"]: r["body"] for r in rows if (r.get("body") or "").strip()}
        _version = version


def get_prompt(key: str, default: str) -> str:
    """The live text of a prompt. Called on every LLM call, so it is cached in-process."""
    if time.monotonic() >= _next_check and enabled():
        # The first load waits, so no call uses a default that has been edited.
        # After that one thread checks and the rest keep using what they have.
        if _lock.acquire(blocking=not _loaded):
            try:
                if time.monotonic() >= _next_check:
                    _refresh()
            except Exception as exc:  # noqa: BLE001
                logger.warning("operation=prompt_refresh status=failed error=%s", str(exc)[:200])
            finally:
                _lock.release()
    return _bodies.get(key) or default


def reset_cache() -> None:
    """Re-read on the next get_prompt. Also how a save reaches this process immediately."""
    global _bodies, _version, _next_check, _loaded
    with _lock:
        _bodies, _version, _next_check, _loaded = {}, None, 0.0, False


def list_prompts() -> list[dict[str, Any]]:
    from app.services.prompt_catalog import CATALOG

    known = {spec.key for spec in CATALOG}
    rows = _client().table(TABLE).select(SUMMARY_COLUMNS).execute().data or []
    return [r for r in rows if r["key"] in known]


def fetch_prompt(key: str) -> dict[str, Any]:
    rows = _client().table(TABLE).select(DETAIL_COLUMNS).eq("key", key).limit(1).execute().data
    if not rows:
        raise PromptNotFound(key)
    return rows[0]


GUIDE_TABLE = "agent_prompt_guides"


def load_guide(key: str, body_hash: str) -> dict[str, str] | None:
    """The saved note for this exact prompt text, or None if there is none (or it is for older text)."""
    rows = _client().table(GUIDE_TABLE).select("body_hash,what,when_to_change").eq("key", key).limit(1).execute().data
    if rows and rows[0]["body_hash"] == body_hash and (rows[0].get("what") or "").strip():
        return {"what": rows[0]["what"], "when": rows[0]["when_to_change"]}
    return None


def store_guide(key: str, body_hash: str, what: str, when: str) -> None:
    _client().table(GUIDE_TABLE).upsert(
        {
            "key": key,
            "body_hash": body_hash,
            "what": what,
            "when_to_change": when,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()


VERSION_TABLE = "agent_prompt_versions"
VERSION_LIMIT = 30


def _record_version(key: str, body: str, editor: str, note: str) -> None:
    """History is best effort: a missing table must not stop a save."""
    try:
        _client().table(VERSION_TABLE).insert(
            {"key": key, "body": body, "note": note[:300], "created_by": editor or None}
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation=prompt_version status=failed key=%s error=%s", key, str(exc)[:200])


def list_versions(key: str) -> list[dict[str, Any]]:
    """Saved versions, newest first."""
    return (
        _client()
        .table(VERSION_TABLE)
        .select("id,body,note,created_by,created_at")
        .eq("key", key)
        .order("created_at", desc=True)
        .limit(VERSION_LIMIT)
        .execute()
        .data
        or []
    )


def _write(key: str, body: str, editor: str, note: str = "") -> dict[str, Any]:
    _client().table(TABLE).update(
        {"body": body, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": editor or None}
    ).eq("key", key).execute()
    reset_cache()
    _record_version(key, body, editor, note)
    return fetch_prompt(key)


def save_prompt(
    key: str, body: str, *, editor: str, expected_updated_at: str | None = None, note: str = ""
) -> dict[str, Any]:
    current = fetch_prompt(key)
    if expected_updated_at and expected_updated_at != current["updated_at"]:
        raise PromptConflict(key)
    return _write(key, body, editor, note)


def reset_prompt(key: str, *, editor: str) -> dict[str, Any]:
    return _write(key, fetch_prompt(key)["default_body"], editor, "Went back to the original")


def _catalog_rows() -> list[dict[str, str]]:
    """Read every default out of the module that defines it."""
    from app.services.prompt_catalog import CATALOG

    rows = []
    for spec in CATALOG:
        try:
            default = getattr(importlib.import_module(spec.module), spec.attr)
        except Exception as exc:  # noqa: BLE001
            logger.warning("operation=prompt_catalog key=%s status=skipped error=%s", spec.key, str(exc)[:200])
            continue
        first_line = default.strip().splitlines()[0][:200] if default.strip() else ""
        rows.append(
            {
                "key": spec.key,
                "group_name": spec.group,
                "name": spec.name,
                "description": spec.description or first_line,
                "source": f"{spec.module}.{spec.attr}",
                "default_body": default,
            }
        )
    return rows


def sync_catalog() -> dict[str, int]:
    """Bring the table in line with code without ever overwriting someone's edit.

    New prompts are inserted. An unedited prompt follows the code. An edited prompt
    keeps its body and only its default moves, so Reset always means the current code.
    """
    columns = "key,body,default_body,group_name,name,description,source,updated_at"
    existing = {r["key"]: r for r in _client().table(TABLE).select(columns).execute().data or []}
    now = datetime.now(timezone.utc).isoformat()
    upserts = []
    for row in _catalog_rows():
        current = existing.get(row["key"])
        if current is None:
            upserts.append({**row, "body": row["default_body"], "updated_at": now})
        elif any(current.get(k) != v for k, v in row.items()):
            if current["body"] != current["default_body"]:
                upserts.append({**row, "body": current["body"], "updated_at": current["updated_at"]})
            else:
                upserts.append({**row, "body": row["default_body"], "updated_at": now})
    # PostgREST needs one column set per batch, and Postgres checks NOT NULL before it
    # resolves a conflict, so every row carries body and updated_at.
    for start in range(0, len(upserts), 25):
        _client().table(TABLE).upsert(upserts[start : start + 25], on_conflict="key").execute()
    if upserts:
        reset_cache()
    return {"catalog": len(existing), "written": len(upserts)}


def sync_catalog_safely() -> None:
    """Startup hook. A missing table only means the migration has not been applied yet."""
    if not enabled():
        return
    try:
        result = sync_catalog()
        logger.info("operation=prompt_sync status=ok written=%s", result["written"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation=prompt_sync status=failed error=%s", str(exc)[:200])
