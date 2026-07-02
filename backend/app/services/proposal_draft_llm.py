"""LLM helpers for proposal drafting — JSON repair without leaking raw errors."""

from __future__ import annotations

import logging
from typing import Any

from app.services import llm
from app.services.llm import LlmError

logger = logging.getLogger(__name__)

SECTION_DRAFT_FAILURE_PLACEHOLDER = (
    "[VERIFY: Section drafting failed — needs manual regeneration]"
)


def is_json_parse_failure(exc: LlmError) -> bool:
    message = str(exc).casefold()
    return "invalid json" in message or "json" in message


async def chat_json_with_repair(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str]:
    """chat_json with one repair pass on JSON parse failure."""
    try:
        return await llm.chat_json(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except LlmError as exc:
        if not is_json_parse_failure(exc):
            raise
        logger.warning("Draft chat_json parse failed — running repair pass: %s", exc)
        repair_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Your previous response was not valid JSON. "
                    "Return ONLY the JSON object described in the system prompt. "
                    "No markdown fences, no commentary."
                ),
            },
        ]
        return await llm.chat_json(
            repair_messages,
            max_tokens=max_tokens,
            temperature=min(temperature, 0.15),
        )
