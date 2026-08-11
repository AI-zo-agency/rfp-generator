"""Anthropic prompt-cache breakpoints, applied at the transport boundary.

The proposal pipeline re-sends the same prompt prefix constantly: a large stable
preamble on every drafting batch, the full prior-sections block on each
subsequent batch, and a verbatim second copy of both whenever the JSON repair
pass fires. Every one of those was billed at the full input rate.

Attaching ``cache_control`` breakpoints makes the repeated prefix cost ~0.1x.
The model receives byte-identical text either way, so nothing about the output
can change — this is purely a billing-side change.

The transform lives here, and is applied once in ``llm._post_chat``, so all 146
call sites benefit without being touched.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.services.llm_pricing import estimate_tokens_from_chars

logger = logging.getLogger(__name__)

# Anthropic allows at most four cache breakpoints per request.
MAX_CACHE_BREAKPOINTS = 4

# Below the model's minimum cacheable prefix Anthropic ignores the breakpoint.
# We skip it explicitly rather than relying on that, so we never emit a
# breakpoint that cannot pay back its write premium.
MIN_CACHEABLE_TOKENS_DEFAULT = 1024
MIN_CACHEABLE_TOKENS_HAIKU = 2048


def supports_prompt_cache(model: str) -> bool:
    """True for Claude models, which are the only ones served ``cache_control``.

    Gemini uses a separate explicit-cache API and Fireworks/Llama has none, so
    both keep the plain-string message shape they have today.
    """
    mid = (model or "").lower()
    return "anthropic" in mid or "claude" in mid


def min_cacheable_tokens(model: str) -> int:
    return (
        MIN_CACHEABLE_TOKENS_HAIKU
        if "haiku" in (model or "").lower()
        else MIN_CACHEABLE_TOKENS_DEFAULT
    )


def message_char_count(message: dict[str, Any]) -> int:
    """Character count of a message whose content may be a string or a part list.

    Once content becomes a list of parts, ``len(content)`` counts parts rather
    than characters — which would silently turn the fallback token estimate into
    a number near zero.
    """
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total
    return 0


def _text_part(text: str, *, cached: bool, ttl_1h: bool) -> dict[str, Any]:
    part: dict[str, Any] = {"type": "text", "text": text}
    if cached:
        cache_control: dict[str, Any] = {"type": "ephemeral"}
        if ttl_1h:
            cache_control["ttl"] = "1h"
        part["cache_control"] = cache_control
    return part


def _content_as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text") or ""
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def normalize_cache_prefix(cache_prefix: str | Sequence[str] | None) -> list[str]:
    """Coerce a prefix argument to an ordered list of non-empty segments."""
    if cache_prefix is None:
        return []
    if isinstance(cache_prefix, str):
        return [cache_prefix] if cache_prefix else []
    return [seg for seg in cache_prefix if seg]


def apply_cache_control(
    messages: list[dict[str, Any]],
    *,
    model: str,
    cache_prefix: str | Sequence[str] | None = None,
    ttl_1h: bool = False,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    """Return messages with cache breakpoints attached.

    Two breakpoint sources, in priority order:

    1. Each system message — constant per node, so it is worth caching on any
       node called more than once inside the TTL window.
    2. ``cache_prefix`` — the stable preamble a high-volume caller has separated
       from its volatile tail. Emitted as cached text parts prepended to the last
       user message, ahead of that message's existing content.

    Passing an ordered *sequence* of prefix segments gives each its own
    breakpoint, which matters when an earlier segment is stable and a later one
    grows. A single breakpoint spanning both would be invalidated every time the
    growing segment changed, taking the stable segment's cache down with it;
    separate breakpoints let the stable one keep hitting.

    Returns the input unchanged (plain string contents intact) when caching is
    disabled or the model does not support it, so non-Claude providers and the
    kill switch both fall back to exactly the previous behaviour.
    """
    segments = normalize_cache_prefix(cache_prefix)
    if not enabled or not supports_prompt_cache(model):
        if segments:
            # Still deliver the text — it is prompt content, not an optimisation.
            return _inline_prefix_without_cache(messages, segments)
        return messages

    minimum = min_cacheable_tokens(model)
    budget = MAX_CACHE_BREAKPOINTS
    out: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system" and isinstance(content, str) and budget > 0:
            if estimate_tokens_from_chars(len(content)) >= minimum:
                out.append(
                    {
                        **message,
                        "content": [_text_part(content, cached=True, ttl_1h=ttl_1h)],
                    }
                )
                budget -= 1
                continue
        out.append(dict(message))

    if segments:
        out = _attach_prefix(
            out,
            segments=segments,
            minimum=minimum,
            ttl_1h=ttl_1h,
            budget=budget,
        )

    return out


def _attach_prefix(
    messages: list[dict[str, Any]],
    *,
    segments: list[str],
    minimum: int,
    ttl_1h: bool,
    budget: int,
) -> list[dict[str, Any]]:
    index = _last_user_index(messages)
    if index is None:
        # No user message to attach to — send the prefix as its own user message
        # rather than dropping prompt content on the floor.
        return [*messages, {"role": "user", "content": "".join(segments)}]

    parts: list[dict[str, Any]] = []
    cumulative_chars = 0
    any_cached = False
    for segment in segments:
        cumulative_chars += len(segment)
        # The minimum applies to the whole prefix up to the breakpoint, not to
        # the segment alone, so this check is cumulative.
        cacheable = (
            budget > 0 and estimate_tokens_from_chars(cumulative_chars) >= minimum
        )
        parts.append(_text_part(segment, cached=cacheable, ttl_1h=ttl_1h))
        if cacheable:
            budget -= 1
            any_cached = True

    if not any_cached:
        return _inline_prefix_without_cache(messages, segments)

    target = messages[index]
    tail = _content_as_text(target.get("content"))
    if tail:
        parts.append(_text_part(tail, cached=False, ttl_1h=ttl_1h))

    updated = list(messages)
    updated[index] = {**target, "content": parts}
    return updated


def inline_cache_prefix(
    messages: list[dict[str, Any]],
    cache_prefix: str | Sequence[str] | None,
) -> list[dict[str, Any]]:
    """Deliver ``cache_prefix`` as ordinary prompt text, uncached.

    For providers with no ``cache_control`` support — currently the Gemini path,
    which has its own explicit-cache API — the prefix must still reach the model
    in the same position, so the prompt is identical to the cached path.
    """
    segments = normalize_cache_prefix(cache_prefix)
    if not segments:
        return messages
    return _inline_prefix_without_cache(messages, segments)


def _inline_prefix_without_cache(
    messages: list[dict[str, Any]],
    segments: list[str],
) -> list[dict[str, Any]]:
    """Concatenate the prefix into the last user message, uncached.

    The prefix is prompt content the caller expects the model to see. When it
    cannot be cached — non-Claude model, kill switch, below the minimum, or out
    of breakpoints — it must still be sent, in the same position, so the prompt
    text is identical to the cached path.
    """
    prefix = "".join(segments)
    index = _last_user_index(messages)
    if index is None:
        return [*messages, {"role": "user", "content": prefix}]
    target = messages[index]
    tail = _content_as_text(target.get("content"))
    merged = f"{prefix}{tail}" if tail else prefix
    updated = list(messages)
    updated[index] = {**target, "content": merged}
    return updated


def _last_user_index(messages: list[dict[str, Any]]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return None
