"""Length-truncated JSON must bump max_tokens instead of failing the same budget twice."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.llm import (
    LlmError,
    bump_max_tokens_after_length_hit,
    chat_json,
)


class BumpMaxTokensTests(unittest.TestCase):
    def test_doubles_with_floor_and_cap(self) -> None:
        self.assertEqual(bump_max_tokens_after_length_hit(4096), 8192)
        self.assertEqual(bump_max_tokens_after_length_hit(2048), 8192)
        self.assertEqual(bump_max_tokens_after_length_hit(8192), 16384)
        self.assertEqual(bump_max_tokens_after_length_hit(16384), 32768)
        self.assertEqual(bump_max_tokens_after_length_hit(32768), 32768)

    def test_lean_scan_nodes_never_climb_to_32k(self) -> None:
        self.assertEqual(
            bump_max_tokens_after_length_hit(4096, node_name="kb_fact_check_section"),
            8192,
        )
        self.assertEqual(
            bump_max_tokens_after_length_hit(8192, node_name="fulfill-scan"),
            8192,
        )
        self.assertEqual(
            bump_max_tokens_after_length_hit(16000, node_name="combined_contradiction_audit"),
            8192,
        )
        from app.services.llm import _should_retry_after_length_truncation

        self.assertFalse(
            _should_retry_after_length_truncation(
                finish_reason="length",
                requested=8192,
                node_name="kb_fact_check_section",
            )
        )


class OpenRouterLengthRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_once_with_bumped_max_tokens_after_length(self) -> None:
        # Salvage closes this to {"workBreakdown": {"packages": []}} — without a
        # length bump we would wrongly accept an empty plan.
        truncated = '{"workBreakdown": {"packages": ['
        posts = AsyncMock(
            side_effect=[
                (truncated, {"finish_reason": "length", "prompt_tokens": 100, "completion_tokens": 4096}),
                (
                    '{"workBreakdown":{"packages":[{"workPackage":"Kickoff","phase":"Discovery"}],"confidence":0.8}}',
                    {"finish_reason": "stop", "prompt_tokens": 100, "completion_tokens": 200},
                ),
            ]
        )
        with (
            patch("app.services.llm._provider_routing", return_value=(None, True, False, False)),
            patch("app.services.llm._openrouter_route", return_value=("sk-test", "anthropic/claude-sonnet-5")),
            patch("app.services.llm._enforce_run_cost_cap"),
            patch("app.services.llm.apply_standing_corrections", new=AsyncMock(side_effect=lambda m, **_: m)),
            patch("app.services.llm._post_chat", new=posts),
            patch("app.services.llm._record_successful_call"),
            patch("app.services.llm.settings") as settings,
        ):
            settings.openrouter_base_url = "https://openrouter.ai/api/v1"
            settings.app_url = "http://localhost"
            settings.app_name = "test"
            settings.gemini_api_key = ""
            settings.llm_prefer_fireworks = False
            parsed, provider = await chat_json(
                [{"role": "user", "content": "plan"}],
                max_tokens=4096,
                node_name="execution_plan",
            )

        self.assertEqual(provider, "openrouter")
        self.assertEqual(parsed["workBreakdown"]["packages"][0]["workPackage"], "Kickoff")
        self.assertEqual(posts.await_count, 2)
        self.assertEqual(posts.await_args_list[0].kwargs.get("max_tokens"), 4096)
        self.assertEqual(posts.await_args_list[1].kwargs.get("max_tokens"), 8192)

class ReinforceUsesBumpedBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reinforcement_bumps_after_invalid_json(self) -> None:
        posts = AsyncMock(
            side_effect=[
                LlmError("LLM returned invalid JSON: {"),
                (
                    '{"ok": true}',
                    {"finish_reason": "stop", "prompt_tokens": 10, "completion_tokens": 5},
                ),
            ]
        )
        with (
            patch("app.services.llm._provider_routing", return_value=(None, True, False, False)),
            patch("app.services.llm._openrouter_route", return_value=("sk-test", "anthropic/claude-sonnet-5")),
            patch("app.services.llm._enforce_run_cost_cap"),
            patch("app.services.llm.apply_standing_corrections", new=AsyncMock(side_effect=lambda m, **_: m)),
            patch("app.services.llm._post_chat", new=posts),
            patch("app.services.llm._record_successful_call"),
            patch("app.services.llm._fireworks_key", return_value=""),
            patch("app.services.llm.settings") as settings,
        ):
            settings.openrouter_base_url = "https://openrouter.ai/api/v1"
            settings.app_url = "http://localhost"
            settings.app_name = "test"
            settings.gemini_api_key = ""
            settings.llm_prefer_fireworks = False
            parsed, provider = await chat_json(
                [{"role": "user", "content": "x"}],
                max_tokens=4096,
                node_name="execution_plan",
            )

        self.assertEqual(provider, "openrouter")
        self.assertEqual(parsed, {"ok": True})
        # First call fails; reinforcement uses bumped budget.
        self.assertEqual(posts.await_args_list[-1].kwargs.get("max_tokens"), 8192)


class LeanEmptyContentNoReinforceTests(unittest.IsolatedAsyncioTestCase):
    async def test_lean_fact_check_skips_reinforce_after_empty_content(self) -> None:
        posts = AsyncMock(side_effect=[LlmError("OpenRouter returned empty content")])
        with (
            patch("app.services.llm._provider_routing", return_value=(None, True, False, False)),
            patch("app.services.llm._openrouter_route", return_value=("sk-test", "anthropic/claude-sonnet-5")),
            patch("app.services.llm._enforce_run_cost_cap"),
            patch("app.services.llm.apply_standing_corrections", new=AsyncMock(side_effect=lambda m, **_: m)),
            patch("app.services.llm._post_chat", new=posts),
            patch("app.services.llm._record_successful_call"),
            patch("app.services.llm._fireworks_key", return_value=""),
            patch("app.services.llm.settings") as settings,
        ):
            settings.openrouter_base_url = "https://openrouter.ai/api/v1"
            settings.app_url = "http://localhost"
            settings.app_name = "test"
            settings.gemini_api_key = ""
            settings.llm_prefer_fireworks = False
            with self.assertRaises(LlmError) as ctx:
                await chat_json(
                    [{"role": "user", "content": "x"}],
                    max_tokens=4096,
                    node_name="kb_fact_check_section",
                )

        self.assertIn("skipped reinforcement", str(ctx.exception))
        self.assertEqual(posts.await_count, 1)


class ContradictionRewriteLeanTests(unittest.TestCase):
    def test_contradiction_rewrite_nodes_use_light_tier_when_lean(self) -> None:
        from app.services.llm import contradiction_rewrite_chat_kwargs

        with patch("app.services.llm.is_lean_fulfill_scan_context", return_value=True):
            kw = contradiction_rewrite_chat_kwargs(
                node_name="scan_rfp_contradiction_rewrite:exec"
            )
        self.assertEqual(kw["tier"], "light")
        self.assertEqual(kw["max_tokens"], 4096)

    def test_contradiction_rewrite_nodes_keep_heavy_outside_lean(self) -> None:
        from app.services.llm import contradiction_rewrite_chat_kwargs

        with patch(
            "app.services.llm.is_lean_fulfill_scan_context", return_value=False
        ), patch("app.services.llm._is_lean_scan_node", return_value=False):
            kw = contradiction_rewrite_chat_kwargs(
                node_name="scan_rfp_contradiction_rewrite:exec"
            )
        self.assertNotIn("tier", kw)
        self.assertEqual(kw["max_tokens"], 16000)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
