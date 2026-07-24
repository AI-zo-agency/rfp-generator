"""Static USD price table for LLM cost instrumentation.

Update rates here when OpenRouter / provider pricing changes.
Amounts are USD per 1,000,000 tokens (input / output).
"""

from __future__ import annotations

from dataclasses import dataclass


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


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    price = resolve_model_price(model)
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    return (inp / 1_000_000.0) * price.input_per_mtok + (
        out / 1_000_000.0
    ) * price.output_per_mtok


def estimate_tokens_from_chars(char_count: int) -> int:
    """Rough token estimate when provider usage is missing (~4 chars/token)."""
    return max(0, int(round(max(0, char_count) / 4.0)))
