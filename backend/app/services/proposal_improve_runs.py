"""Remember Ask Ralph section-improve runs so a page refresh can pick them back up.

The improve endpoint is one long synchronous request. When the user reloads the
page mid-run the browser drops the response, but the backend keeps working —
so each run (keyed by a client-generated run id) records its state and final
response here, and the reloaded page polls for it instead of losing the turn.

Redis (with TTL) when configured so any web process can answer the poll; an
in-process dict otherwise (local dev without Redis).
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.config import settings

_REDIS_KEY_PREFIX = "zo:improve-run:"
# Long enough that a preview awaiting Apply can still be applied after a refresh.
_RUN_TTL_SEC = 6 * 3600
# A run still "running" past this can no longer be alive (the request would
# have timed out or the process restarted) — report it as interrupted.
_STALE_RUNNING_SEC = 45 * 60

_memory_runs: dict[str, dict[str, Any]] = {}


def _key(rfp_id: str, run_id: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{rfp_id}:{run_id}"


def _prune_memory() -> None:
    cutoff = time.time() - _RUN_TTL_SEC
    for key in [k for k, v in _memory_runs.items() if v.get("startedAt", 0) < cutoff]:
        _memory_runs.pop(key, None)


async def _write(rfp_id: str, run_id: str, record: dict[str, Any]) -> None:
    key = _key(rfp_id, run_id)
    if settings.celery_enabled:
        from app.services.redis_client import get_redis, is_redis_unavailable, reset_redis_client

        try:
            await get_redis().set(key, json.dumps(record), ex=_RUN_TTL_SEC)
            return
        except Exception as exc:  # noqa: BLE001
            if not is_redis_unavailable(exc):
                raise
            reset_redis_client()
    _prune_memory()
    _memory_runs[key] = record


async def _read(rfp_id: str, run_id: str) -> dict[str, Any] | None:
    key = _key(rfp_id, run_id)
    if settings.celery_enabled:
        from app.services.redis_client import get_redis, is_redis_unavailable, reset_redis_client

        try:
            raw = await get_redis().get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            if not is_redis_unavailable(exc):
                raise
            reset_redis_client()
    return _memory_runs.get(key)


async def mark_improve_run_started(rfp_id: str, run_id: str, section_id: str) -> None:
    await _write(
        rfp_id,
        run_id,
        {"status": "running", "sectionId": section_id, "startedAt": time.time()},
    )


async def finish_improve_run(
    rfp_id: str,
    run_id: str,
    section_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    prior = await _read(rfp_id, run_id) or {}
    record: dict[str, Any] = {
        "status": "completed" if error is None else "failed",
        "sectionId": section_id,
        "startedAt": prior.get("startedAt", time.time()),
        "finishedAt": time.time(),
    }
    if error is None:
        record["result"] = result
    else:
        record["detail"] = error
    await _write(rfp_id, run_id, record)


async def get_improve_run(rfp_id: str, run_id: str) -> dict[str, Any] | None:
    record = await _read(rfp_id, run_id)
    if record is None:
        return None
    if (
        record.get("status") == "running"
        and time.time() - float(record.get("startedAt") or 0) > _STALE_RUNNING_SEC
    ):
        return {
            **record,
            "status": "failed",
            "detail": "Ralph stopped before this request finished. Nothing was changed.",
        }
    return record
