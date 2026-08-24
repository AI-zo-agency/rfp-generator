"""Lazy shared async Redis client — only connected when settings.celery_enabled.

Used by proposal_job_runner.py (job/lock tracking) and
proposal_generation_cancel.py (cross-process cooperative cancel flag).
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        if not settings.celery_enabled:
            raise RuntimeError(
                "get_redis() called without REDIS_URL configured — "
                "callers must check settings.celery_enabled first."
            )
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client
