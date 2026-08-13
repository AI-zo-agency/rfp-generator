"""Run the API locally: ``python -m app`` (default port 8001 from settings/PORT)."""

from __future__ import annotations

import logging

import uvicorn

from app.core.config import settings

logger = logging.getLogger(__name__)


def main() -> None:
    port = settings.port
    logger.info("Starting uvicorn host=127.0.0.1 port=%s reload=True", port)
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        reload=True,
    )


if __name__ == "__main__":
    main()
