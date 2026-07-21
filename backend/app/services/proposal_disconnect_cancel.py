"""When the browser/proxy closes, stop LLM + Supermemory for that RFP."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Request

from app.services.proposal_generation_cancel import request_generation_cancel

logger = logging.getLogger(__name__)


@asynccontextmanager
async def cancel_generation_on_disconnect(rfp_id: str, request: Request | None):
    """Poll client disconnect; set cooperative cancel so in-flight work stops."""
    if request is None:
        yield
        return

    async def _watch() -> None:
        while True:
            if await request.is_disconnected():
                logger.warning(
                    "Client disconnected for %s — requesting generation cancel",
                    rfp_id,
                )
                request_generation_cancel(rfp_id)
                return
            await asyncio.sleep(0.35)

    task = asyncio.create_task(_watch())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
