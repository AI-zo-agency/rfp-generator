import asyncio
import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_KEY_MARKERS = (
    "your_openrouter_key",
    "changeme",
    "replace_me",
    "xxx",
)


class LlmError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    return bool(_openrouter_key() or _fireworks_key())


def _openrouter_key() -> str:
    key = settings.openrouter_api_key.strip()
    if not key or _is_placeholder_key(key):
        return ""
    return key


def _fireworks_key() -> str:
    return settings.fireworks_api_key.strip()


def _is_placeholder_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in _PLACEHOLDER_KEY_MARKERS:
        return True
    return lowered.startswith("your_") or lowered.startswith("paste_")


async def _post_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    provider: str,
    extra_headers: dict[str, str] | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    logger.info(
        "LLM request: provider=%s model=%s messages=%d",
        provider,
        model,
        len(messages),
    )

    last_error: LlmError | None = None
    for attempt in range(4):
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code == 429 and attempt < 3:
            wait_s = 2 ** (attempt + 1)
            logger.warning(
                "LLM rate limited (%s), retrying in %ds (attempt %d/3)",
                provider,
                wait_s,
                attempt + 1,
            )
            await asyncio.sleep(wait_s)
            continue

        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            logger.warning(
                "LLM error: provider=%s model=%s status=%s detail=%s",
                provider,
                model,
                response.status_code,
                detail[:300],
            )
            last_error = LlmError(
                f"{provider} API error ({response.status_code}): {detail}",
                status_code=response.status_code,
            )
            break

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"{provider} returned an unexpected response shape") from exc

        if not isinstance(content, str) or not content.strip():
            raise LlmError(f"{provider} returned empty content")

        logger.info(
            "LLM success: provider=%s model=%s response_chars=%d",
            provider,
            model,
            len(content),
        )
        return content.strip()

    if last_error:
        raise last_error
    raise LlmError(f"{provider} request failed after retries", status_code=429)


async def chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str]:
    errors: list[str] = []

    openrouter_key = _openrouter_key()
    if openrouter_key:
        try:
            raw = await _post_chat(
                base_url=settings.openrouter_base_url,
                api_key=openrouter_key,
                model=settings.openrouter_model,
                messages=messages,
                provider="OpenRouter",
                extra_headers={
                    "HTTP-Referer": settings.app_url,
                    "X-Title": settings.app_name,
                },
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return _parse_json_response(raw), "openrouter"
        except LlmError as exc:
            errors.append(str(exc))
            logger.info("OpenRouter failed, trying Fireworks fallback")

    fireworks_key = _fireworks_key()
    if fireworks_key:
        try:
            raw = await _post_chat(
                base_url=settings.fireworks_base_url,
                api_key=fireworks_key,
                model=settings.fireworks_model,
                messages=messages,
                provider="Fireworks",
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return _parse_json_response(raw), "fireworks"
        except LlmError as exc:
            errors.append(str(exc))

    if not errors:
        raise LlmError(
            "No LLM API key configured. Set OPENROUTER_API_KEY (primary) or FIREWORKS_API_KEY (fallback).",
            status_code=503,
        )

    raise LlmError(
        "All configured LLM providers failed: " + "; ".join(errors),
        status_code=502,
    )


def _parse_json_response(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"LLM returned invalid JSON: {raw[:200]}") from exc

    if not isinstance(parsed, dict):
        raise LlmError("LLM JSON response must be an object")
    return parsed
