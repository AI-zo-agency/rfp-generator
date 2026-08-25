"""Cooperative stop for long proposal runs (OpenRouter, Supermemory, phased pipeline).

request_generation_cancel/clear_generation_cancel/is_generation_cancelled stay
synchronous and in-process — the fast, always-correct signal within one
process (e.g. local dev without Redis).

When settings.celery_enabled, the actual pipeline work runs in a SEPARATE
Celery worker process that does not share this module's in-memory set with
the web process that received POST /stop. check_generation_cancelled (async,
used by the hot ~0.35s poll loop in run_with_generation_cancel — see
llm.py/supermemory.py) additionally consults a Redis-backed flag in that
case, through a short local cache so a job doing several concurrent
LLM/Supermemory calls doesn't hammer Redis on every poll tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
from typing import Awaitable, Callable, TypeVar

from app.core.config import settings
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

_active_rfp_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "proposal_active_rfp_id", default=None
)
_cancel_requested: set[str] = set()

_REDIS_CANCEL_PREFIX = "zo:cancel:"
_REDIS_CANCEL_TTL_SEC = 3600
_CANCEL_CACHE_TTL_SEC = 1.0
# rfp_id -> (checked_at_monotonic, is_cancelled) — local cache in front of Redis
_redis_cancel_cache: dict[str, tuple[float, bool]] = {}

T = TypeVar("T")


class ProposalGenerationCancelled(ProposalError):
    def __init__(self, message: str = "Proposal generation stopped.") -> None:
        super().__init__(message, status_code=409)


def bind_active_rfp(rfp_id: str) -> contextvars.Token[str | None]:
    return _active_rfp_id.set(rfp_id)


def unbind_active_rfp(token: contextvars.Token[str | None]) -> None:
    _active_rfp_id.reset(token)


def get_active_rfp_id() -> str:
    return str(_active_rfp_id.get() or "")


def _sync_redis_flag(rfp_id: str, *, cancelled: bool) -> None:
    """Best-effort fire-and-forget Redis write for cross-process (Celery
    worker) visibility. Never blocks or raises for the caller — a missed
    write just means the worker's next poll tick reads stale state for up
    to _CANCEL_CACHE_TTL_SEC longer, not a correctness break."""
    if not settings.celery_enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _write() -> None:
        from app.services.redis_client import get_redis

        key = f"{_REDIS_CANCEL_PREFIX}{rfp_id}"
        try:
            if cancelled:
                await get_redis().setex(key, _REDIS_CANCEL_TTL_SEC, "1")
            else:
                await get_redis().delete(key)
        except Exception:  # noqa: BLE001 — best-effort only
            logger.warning("Redis cancel-flag sync failed for %s", rfp_id, exc_info=True)

    loop.create_task(_write())
    _redis_cancel_cache[rfp_id] = (time.monotonic(), cancelled)


def request_generation_cancel(rfp_id: str) -> None:
    _cancel_requested.add(rfp_id)
    logger.warning("Generation cancel requested for %s", rfp_id)
    _sync_redis_flag(rfp_id, cancelled=True)


def clear_generation_cancel(rfp_id: str) -> None:
    _cancel_requested.discard(rfp_id)
    _sync_redis_flag(rfp_id, cancelled=False)


async def aclear_generation_cancel(rfp_id: str) -> None:
    """Clear the cancel flag and AWAIT the Redis delete before returning.

    The fire-and-forget clear in ``clear_generation_cancel`` can race the very
    first cross-process cancel check when a fresh run starts — the Celery worker
    reads a stale ``zo:cancel:{rfp_id}`` = 1 (left by an earlier Stop or a
    killed worker) before the delete lands, and aborts the new run on the spot
    (the "no logs when I start Complete Scan" symptom). Awaiting the delete at
    task start removes that race deterministically.
    """
    _cancel_requested.discard(rfp_id)
    _redis_cancel_cache[rfp_id] = (time.monotonic(), False)
    if not settings.celery_enabled:
        return
    try:
        from app.services.redis_client import get_redis

        await get_redis().delete(f"{_REDIS_CANCEL_PREFIX}{rfp_id}")
    except Exception:  # noqa: BLE001 — best-effort; never block a run on Redis
        logger.warning("Redis cancel-flag clear failed for %s", rfp_id, exc_info=True)


def is_generation_cancelled(rfp_id: str | None = None) -> bool:
    """In-process check only — see check_generation_cancelled for the
    Redis-aware async version the actual poll loop uses."""
    rid = rfp_id or _active_rfp_id.get()
    return bool(rid and rid in _cancel_requested)


async def _is_generation_cancelled_cross_process(rid: str) -> bool:
    now = time.monotonic()
    cached = _redis_cancel_cache.get(rid)
    if cached and now - cached[0] < _CANCEL_CACHE_TTL_SEC:
        return cached[1]
    from app.services.redis_client import get_redis

    try:
        val = await get_redis().get(f"{_REDIS_CANCEL_PREFIX}{rid}")
    except Exception:  # noqa: BLE001 — fail open, don't block work on a Redis hiccup
        logger.warning("Redis cancel-flag read failed for %s", rid, exc_info=True)
        return False
    cancelled = bool(val)
    _redis_cancel_cache[rid] = (now, cancelled)
    return cancelled


async def check_generation_cancelled(rfp_id: str | None = None) -> None:
    rid = rfp_id or _active_rfp_id.get()
    if not rid:
        return
    if rid in _cancel_requested:
        raise ProposalGenerationCancelled()
    if settings.celery_enabled and await _is_generation_cancelled_cross_process(rid):
        raise ProposalGenerationCancelled()


async def check_cancelled_for_active() -> None:
    await check_generation_cancelled(_active_rfp_id.get())


async def run_with_generation_cancel(
    factory: Callable[[], Awaitable[T]],
    *,
    poll_interval_s: float = 0.35,
) -> T:
    """Run an awaitable; raise if user hits Stop while it is in flight."""
    await check_cancelled_for_active()
    task = asyncio.create_task(factory())
    try:
        while not task.done():
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=poll_interval_s)
            except asyncio.TimeoutError:
                await check_cancelled_for_active()
        return task.result()
    except ProposalGenerationCancelled:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise
