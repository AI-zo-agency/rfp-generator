"""Claude Sonnet 5 / Opus 5 / Fable 5 / Mythos 5 / Opus 4.7 / 4.8 return a 400
if `temperature` (or any other sampling param) is sent at all — adaptive
thinking replaced fixed sampling on this generation. Opus/Sonnet 4.6 and
older still accept it. OpenRouter forwards Anthropic's own validation as-is,
so switching LLM_HEAVY_MODEL to "anthropic/claude-sonnet-5" would have
broken every proposal-pipeline LLM call the moment this repo's config
picked it up, since _post_chat always sent `temperature` unconditionally.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from app.services import llm
from app.services.llm import _claude_rejects_sampling_params


class ClaudeRejectsSamplingParamsTests(unittest.TestCase):
    def test_current_env_heavy_model_rejects(self) -> None:
        """LLM_HEAVY_MODEL in this repo's .env — must be detected."""
        self.assertTrue(_claude_rejects_sampling_params("anthropic/claude-sonnet-5"))

    def test_current_env_light_model_still_allows(self) -> None:
        """LLM_LIGHT_MODEL in this repo's .env — must NOT be affected."""
        self.assertFalse(_claude_rejects_sampling_params("anthropic/claude-haiku-4.5"))

    def test_rejects_five_generation_models(self) -> None:
        for model in [
            "anthropic/claude-sonnet-5",
            "anthropic/claude-opus-5",
            "anthropic/claude-fable-5",
            "anthropic/claude-mythos-5",
            "claude-sonnet-5",
        ]:
            with self.subTest(model=model):
                self.assertTrue(_claude_rejects_sampling_params(model))

    def test_rejects_opus_4_7_and_4_8_both_separators(self) -> None:
        for model in [
            "anthropic/claude-opus-4.7",
            "anthropic/claude-opus-4.8",
            "claude-opus-4-7",
            "claude-opus-4-8",
        ]:
            with self.subTest(model=model):
                self.assertTrue(_claude_rejects_sampling_params(model))

    def test_allows_4_6_and_older(self) -> None:
        """4.6 still allows sampling — must not be swept up by the "-5" check
        or confused with 4.7/4.8."""
        for model in [
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-opus-4.6",
            "anthropic/claude-sonnet-4.5",
            "anthropic/claude-sonnet-4",
            "claude-sonnet-4-6",
        ]:
            with self.subTest(model=model):
                self.assertFalse(_claude_rejects_sampling_params(model))

    def test_does_not_false_positive_on_a_hypothetical_sonnet_15(self) -> None:
        """A trailing "-5" must mean generation 5, not just any digit string
        ending in 5 — endswith("-5") is the exact boundary being tested."""
        self.assertFalse(_claude_rejects_sampling_params("anthropic/claude-sonnet-15"))

    def test_non_anthropic_models_are_never_affected(self) -> None:
        for model in [
            "google/gemini-2.5-flash",
            "accounts/fireworks/models/minimax-m3",
            "openai/gpt-5",
            "",
        ]:
            with self.subTest(model=model):
                self.assertFalse(_claude_rejects_sampling_params(model))


class PostChatOmitsTemperatureTests(unittest.IsolatedAsyncioTestCase):
    async def _post_chat(self, model: str):
        captured: dict = {}

        async def fake_post(_self, url, *, headers, json):  # noqa: A002 - matches httpx signature
            captured["url"] = url
            captured["body"] = json
            response = mock.Mock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
            return response

        with mock.patch.object(llm.httpx.AsyncClient, "post", new=fake_post):
            await llm._post_chat(
                base_url="https://example.invalid",
                api_key="k",
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                provider="TestProvider",
                max_tokens=128,
                temperature=0.35,
            )
        return captured["body"]

    async def test_temperature_omitted_for_sonnet_5(self) -> None:
        body = await self._post_chat("anthropic/claude-sonnet-5")
        self.assertNotIn("temperature", body, f"body sent to Sonnet 5: {json.dumps(body)}")

    async def test_temperature_included_for_sonnet_4_6(self) -> None:
        body = await self._post_chat("anthropic/claude-sonnet-4.6")
        self.assertEqual(body.get("temperature"), 0.35)

    async def test_temperature_included_for_non_claude_model(self) -> None:
        body = await self._post_chat("google/gemini-2.5-flash")
        self.assertEqual(body.get("temperature"), 0.35)


if __name__ == "__main__":
    unittest.main()
