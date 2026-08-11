"""Prompt-cache breakpoints and cache-aware cost accounting."""

from __future__ import annotations

import pytest

from app.services.llm_pricing import (
    CACHE_READ_MULTIPLIER,
    estimate_cost_usd,
    resolve_model_price,
    split_cached_input_tokens,
)
from app.services.llm_prompt_cache import (
    MAX_CACHE_BREAKPOINTS,
    apply_cache_control,
    inline_cache_prefix,
    message_char_count,
    min_cacheable_tokens,
)

CLAUDE = "anthropic/claude-sonnet-4"
GEMINI = "gemini-2.0-flash"

# Comfortably above the 1024-token Sonnet minimum (~4 chars/token).
BIG = "x" * 8000
SMALL = "tiny system prompt"


def _cached_parts(message: dict) -> list[dict]:
    content = message["content"]
    assert isinstance(content, list)
    return [p for p in content if "cache_control" in p]


class TestBreakpoints:
    def test_system_message_gets_breakpoint_for_claude(self) -> None:
        out = apply_cache_control(
            [{"role": "system", "content": BIG}, {"role": "user", "content": "hi"}],
            model=CLAUDE,
        )
        assert _cached_parts(out[0])[0]["cache_control"] == {"type": "ephemeral"}
        assert out[0]["content"][0]["text"] == BIG

    def test_non_claude_model_keeps_plain_strings(self) -> None:
        messages = [{"role": "system", "content": BIG}, {"role": "user", "content": "hi"}]
        out = apply_cache_control(messages, model=GEMINI)
        assert out == messages
        assert isinstance(out[0]["content"], str)

    def test_kill_switch_restores_previous_behaviour(self) -> None:
        messages = [{"role": "system", "content": BIG}, {"role": "user", "content": "hi"}]
        out = apply_cache_control(messages, model=CLAUDE, enabled=False)
        assert out == messages

    def test_below_minimum_gets_no_breakpoint(self) -> None:
        out = apply_cache_control(
            [{"role": "system", "content": SMALL}, {"role": "user", "content": "hi"}],
            model=CLAUDE,
        )
        assert out[0]["content"] == SMALL

    def test_haiku_has_a_higher_minimum_than_sonnet(self) -> None:
        assert min_cacheable_tokens("anthropic/claude-haiku-4.5") > min_cacheable_tokens(
            CLAUDE
        )

    def test_never_exceeds_four_breakpoints(self) -> None:
        messages = [{"role": "system", "content": BIG} for _ in range(7)]
        messages.append({"role": "user", "content": "go"})
        out = apply_cache_control(messages, model=CLAUDE, cache_prefix=BIG)
        total = sum(
            len(_cached_parts(m)) for m in out if isinstance(m.get("content"), list)
        )
        assert total <= MAX_CACHE_BREAKPOINTS

    def test_ttl_1h_is_marked_on_the_breakpoint(self) -> None:
        out = apply_cache_control(
            [{"role": "system", "content": BIG}, {"role": "user", "content": "hi"}],
            model=CLAUDE,
            ttl_1h=True,
        )
        assert _cached_parts(out[0])[0]["cache_control"]["ttl"] == "1h"


class TestCachePrefix:
    def test_prefix_is_cached_and_precedes_the_volatile_tail(self) -> None:
        out = apply_cache_control(
            [{"role": "user", "content": "VOLATILE"}],
            model=CLAUDE,
            cache_prefix=BIG,
        )
        parts = out[0]["content"]
        assert parts[0]["text"] == BIG
        assert "cache_control" in parts[0]
        assert parts[1]["text"] == "VOLATILE"
        assert "cache_control" not in parts[1]

    def test_prompt_text_is_identical_cached_or_not(self) -> None:
        """The prefix is prompt content, not an optimisation — it must always be sent.

        A dropped prefix would silently strip context from the model, which is the
        one way this change could damage output quality.
        """
        messages = [{"role": "user", "content": "VOLATILE"}]
        cached = apply_cache_control(messages, model=CLAUDE, cache_prefix=BIG)
        uncached = apply_cache_control(messages, model=GEMINI, cache_prefix=BIG)

        cached_text = "".join(p["text"] for p in cached[0]["content"])
        assert cached_text == uncached[0]["content"] == BIG + "VOLATILE"

    def test_prefix_below_minimum_is_still_delivered(self) -> None:
        out = apply_cache_control(
            [{"role": "user", "content": "VOLATILE"}],
            model=CLAUDE,
            cache_prefix=SMALL,
        )
        assert out[0]["content"] == SMALL + "VOLATILE"

    def test_prefix_attaches_to_the_last_user_message(self) -> None:
        out = apply_cache_control(
            [
                {"role": "user", "content": "FIRST"},
                {"role": "assistant", "content": "REPLY"},
                {"role": "user", "content": "SECOND"},
            ],
            model=CLAUDE,
            cache_prefix=BIG,
        )
        assert out[0]["content"] == "FIRST"
        assert out[2]["content"][0]["text"] == BIG
        assert out[2]["content"][1]["text"] == "SECOND"

    def test_inline_cache_prefix_is_a_noop_without_a_prefix(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        assert inline_cache_prefix(messages, None) == messages


class TestMessageCharCount:
    def test_counts_characters_not_parts(self) -> None:
        """len() on a content list counts parts, which would zero out the estimate."""
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "abc"},
                {"type": "text", "text": "de"},
            ],
        }
        assert message_char_count(message) == 5

    def test_handles_plain_strings(self) -> None:
        assert message_char_count({"role": "user", "content": "abcde"}) == 5


class TestUsageSplit:
    def test_anthropic_shape_sums_to_total(self) -> None:
        """Anthropic's input_tokens EXCLUDES cache counts."""
        uncached, write, read = split_cached_input_tokens(
            {
                "input_tokens": 100,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 4000,
            }
        )
        assert (uncached, write, read) == (100, 500, 4000)

    def test_openrouter_shape_does_not_double_count(self) -> None:
        """OpenRouter's prompt_tokens INCLUDES cached reads, so they must be subtracted."""
        uncached, write, read = split_cached_input_tokens(
            {
                "prompt_tokens": 4600,
                "prompt_tokens_details": {"cached_tokens": 4000},
            }
        )
        assert (uncached, write, read) == (600, 0, 4000)
        assert uncached + write + read == 4600

    def test_uncached_call_is_unchanged(self) -> None:
        assert split_cached_input_tokens({"prompt_tokens": 1234}) == (1234, 0, 0)

    def test_missing_usage_is_zeroed(self) -> None:
        assert split_cached_input_tokens({}) == (0, 0, 0)


class TestPricing:
    def test_zero_cache_tokens_costs_exactly_what_it_did_before(self) -> None:
        price = resolve_model_price(CLAUDE)
        expected = (1000 / 1e6) * price.input_per_mtok + (500 / 1e6) * price.output_per_mtok
        assert estimate_cost_usd(
            model=CLAUDE, input_tokens=1000, output_tokens=500
        ) == pytest.approx(expected)

    def test_cache_read_is_a_tenth_of_input(self) -> None:
        price = resolve_model_price(CLAUDE)
        cost = estimate_cost_usd(
            model=CLAUDE,
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
        )
        assert cost == pytest.approx(price.input_per_mtok * CACHE_READ_MULTIPLIER)

    def test_cache_write_carries_the_expected_premium(self) -> None:
        price = resolve_model_price(CLAUDE)
        five_min = estimate_cost_usd(
            model=CLAUDE,
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
        )
        one_hour = estimate_cost_usd(
            model=CLAUDE,
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
            cache_ttl_1h=True,
        )
        assert five_min == pytest.approx(price.input_per_mtok * 1.25)
        assert one_hour == pytest.approx(price.input_per_mtok * 2.0)

    def test_caching_a_repeated_prefix_is_cheaper_than_resending_it(self) -> None:
        """The whole point, stated as a test."""
        prefix = 10_000
        calls = 10

        uncached = sum(
            estimate_cost_usd(model=CLAUDE, input_tokens=prefix, output_tokens=0)
            for _ in range(calls)
        )
        cached = estimate_cost_usd(
            model=CLAUDE, input_tokens=0, output_tokens=0,
            cache_creation_input_tokens=prefix,
        ) + sum(
            estimate_cost_usd(
                model=CLAUDE, input_tokens=0, output_tokens=0,
                cache_read_input_tokens=prefix,
            )
            for _ in range(calls - 1)
        )
        assert cached < uncached * 0.25
