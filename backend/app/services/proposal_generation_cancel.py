"""Cooperative stop for long proposal runs (OpenRouter, Supermemory, phased pipeline)."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from typing import Awaitable, Callable, TypeVar

from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

_active_rfp_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "proposal_active_rfp_id", default=None
)
_cancel_requested: set[str] = set()

T = TypeVar("T")


class ProposalGenerationCancelled(ProposalError):
    def __init__(self, message: str = "Proposal generation stopped.") -> None:
        super().__init__(message, status_code=409)


def bind_active_rfp(rfp_id: str) -> contextvars.Token[str | None]:
    return _active_rfp_id.set(rfp_id)


def unbind_active_rfp(token: contextvars.Token[str | None]) -> None:
    _active_rfp_id.reset(token)


def request_generation_cancel(rfp_id: str) -> None:
    _cancel_requested.add(rfp_id)
    logger.warning("Generation cancel requested for %s", rfp_id)


def clear_generation_cancel(rfp_id: str) -> None:
    _cancel_requested.discard(rfp_id)


def is_generation_cancelled(rfp_id: str | None = None) -> bool:
    rid = rfp_id or _active_rfp_id.get()
    return bool(rid and rid in _cancel_requested)


async def check_generation_cancelled(rfp_id: str | None = None) -> None:
    if is_generation_cancelled(rfp_id):
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
