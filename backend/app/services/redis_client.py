"""Lazy async Redis client — only connected when settings.celery_enabled.

Used by proposal_job_runner.py (job/lock tracking) and
proposal_generation_cancel.py (cross-process cooperative cancel flag).

Cached per event loop, not as a single process-wide singleton: the FastAPI
backend runs one persistent loop for its whole lifetime (single client,
reused forever — same as before), but each Celery task runs its own fresh
loop via `asyncio.run()` (see celery_app.py). A redis.asyncio.Redis
connection is bound to the loop it was created on — reusing one across a
closed loop raises "Future attached to a different loop" / "Event loop is
closed" the moment a later task tries to use it. Detecting a loop change and
handing back a fresh client fixes that without leaking connections
unboundedly (only ever one cached client at a time, not one per task ever
run).
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from app.core.config import settings

_client: Redis | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_redis() -> Redis:
    global _client, _client_loop
    if not settings.celery_enabled:
        raise RuntimeError(
            "get_redis() called without REDIS_URL configured — "
            "callers must check settings.celery_enabled first."
        )
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
        _client_loop = loop
    return _client
