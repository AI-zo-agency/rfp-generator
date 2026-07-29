"""LangSmith tracing bootstrap for LangChain / LangGraph / @traceable LLM calls.

This app does not use the Claude Agent SDK — tracing is enabled by syncing
LANGSMITH_* into os.environ (LangSmith reads process env, not pydantic Settings)
and by TracingMiddleware + @traceable on custom httpx LLM helpers.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _strip_env_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


def configure_langsmith_tracing() -> bool:
    """Push Settings LangSmith fields into os.environ and report status.

    Returns True when tracing is enabled and an API key is present.
    Safe to call multiple times (e.g. reload).
    """
    from app.core.config import settings

    if not settings.langsmith_tracing:
        logger.info("LangSmith tracing disabled (LANGSMITH_TRACING not true)")
        return False

    api_key = _strip_env_quotes(settings.langsmith_api_key)
    if not api_key:
        logger.warning(
            "LANGSMITH_TRACING=true but LANGSMITH_API_KEY is empty — tracing not enabled"
        )
        return False

    project = _strip_env_quotes(settings.langsmith_project) or "proposal generation"
    endpoint = _strip_env_quotes(settings.langsmith_endpoint) or (
        "https://api.smith.langchain.com"
    )

    os.environ["LANGSMITH_TRACING"] = "true"
    # Legacy alias still checked by some LangChain versions.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGSMITH_ENDPOINT"] = endpoint
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ.setdefault("LANGCHAIN_CALLBACKS_BACKGROUND", "true")

    logger.info(
        "LangSmith tracing enabled project=%s endpoint=%s",
        project,
        endpoint,
    )
    return True
