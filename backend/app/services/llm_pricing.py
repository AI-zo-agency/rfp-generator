"""Static USD price table for LLM cost instrumentation.

Update rates here when OpenRouter / provider pricing changes.
Amounts are USD per 1,000,000 tokens (input / output).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    label: str


# Approximate OpenRouter / provider list prices (USD / 1M tokens).
# Keys are lowercase substrings matched against the model id (longest match wins).
_MODEL_PRICES: tuple[tuple[str, ModelPrice], ...] = (
    ("claude-sonnet-4", ModelPrice(3.0, 15.0, "anthropic/claude-sonnet-4")),
    ("claude-sonnet", ModelPrice(3.0, 15.0, "anthropic/claude-sonnet")),
    ("claude-haiku-4.5", ModelPrice(1.0, 5.0, "anthropic/claude-haiku-4.5")),
    ("claude-haiku", ModelPrice(1.0, 5.0, "anthropic/claude-haiku")),
    ("claude-opus", ModelPrice(15.0, 75.0, "anthropic/claude-opus")),
    ("gemini-2.0-flash", ModelPrice(0.10, 0.40, "gemini-2.0-flash")),
    ("gemini-2.5-flash", ModelPrice(0.15, 0.60, "gemini-2.5-flash")),
    ("gemini-flash", ModelPrice(0.10, 0.40, "gemini-flash")),
    ("gemini", ModelPrice(0.50, 1.50, "gemini")),
    ("llama-v3p3-70b", ModelPrice(0.90, 0.90, "fireworks/llama-v3p3-70b")),
    ("llama", ModelPrice(0.90, 0.90, "llama")),
)

# Fallback when model id is unknown — conservative mid-tier estimate.
_DEFAULT_PRICE = ModelPrice(3.0, 15.0, "unknown")


def resolve_model_price(model: str) -> ModelPrice:
    mid = (model or "").strip().lower()
    if not mid:
        return _DEFAULT_PRICE
    best: ModelPrice | None = None
    best_len = -1
    for key, price in _MODEL_PRICES:
        if key in mid and len(key) > best_len:
            best = price
            best_len = len(key)
    return best or _DEFAULT_PRICE


# Anthropic prompt-cache multipliers, applied to the model's base input rate.
# A 5-minute ephemeral write costs 1.25x base; a 1-hour write costs 2x. Reads of
# either cost 0.1x. Expressed as multipliers rather than absolute rates so the
# table above stays the single place a price is written down.
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.0
CACHE_READ_MULTIPLIER = 0.1


def cache_write_multiplier(*, ttl_1h: bool = False) -> float:
    return CACHE_WRITE_MULTIPLIER_1H if ttl_1h else CACHE_WRITE_MULTIPLIER_5M


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_ttl_1h: bool = False,
) -> float:
    """Cost in USD for one call.

    ``input_tokens`` must be the *uncached* prompt tokens only. Anthropic reports
    cache creation and cache read counts separately from ``prompt_tokens``, so
    passing the raw prompt count alongside non-zero cache counts would double-bill
    the cached portion at the full input rate — which is exactly the error that
    would make caching look like it saved nothing.
    """
    price = resolve_model_price(model)
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    cache_write = max(0, int(cache_creation_input_tokens))
    cache_read = max(0, int(cache_read_input_tokens))

    write_rate = price.input_per_mtok * cache_write_multiplier(ttl_1h=cache_ttl_1h)
    read_rate = price.input_per_mtok * CACHE_READ_MULTIPLIER

    return (
        (inp / 1_000_000.0) * price.input_per_mtok
        + (out / 1_000_000.0) * price.output_per_mtok
        + (cache_write / 1_000_000.0) * write_rate
        + (cache_read / 1_000_000.0) * read_rate
    )


def estimate_tokens_from_chars(char_count: int) -> int:
    """Rough token estimate when provider usage is missing (~4 chars/token)."""
    return max(0, int(round(max(0, char_count) / 4.0)))


def split_cached_input_tokens(usage: dict[str, Any]) -> tuple[int, int, int]:
    """Split a provider usage block into (uncached_input, cache_write, cache_read).

    Providers disagree on shape. Anthropic reports ``cache_creation_input_tokens``
    and ``cache_read_input_tokens`` alongside an ``input_tokens`` that EXCLUDES
    them. OpenRouter normalises to the OpenAI shape, where cached reads appear in
    ``prompt_tokens_details.cached_tokens`` and ``prompt_tokens`` INCLUDES them.

    Getting this backwards is the difference between measuring a real saving and
    inventing one, so the rule here is: whatever the source, the returned triple
    never double-counts, and the three parts always sum to the total prompt
    tokens the provider billed.
    """
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}

    cache_read = _first_int(
        usage.get("cache_read_input_tokens"),
        details.get("cached_tokens"),
    )
    cache_write = _first_int(
        usage.get("cache_creation_input_tokens"),
        details.get("cache_creation_tokens"),
    )

    total_prompt = _first_int(usage.get("prompt_tokens"), usage.get("input_tokens"))

    # Anthropic-native: input_tokens excludes cache counts, so the total is the sum.
    # OpenAI/OpenRouter: prompt_tokens already includes them, so subtract.
    if total_prompt >= cache_read + cache_write:
        uncached = total_prompt - cache_read - cache_write
    else:
        uncached = total_prompt
    return max(0, uncached), max(0, cache_write), max(0, cache_read)


def _first_int(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0
