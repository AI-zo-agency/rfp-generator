"""A network timeout must not bypass every fallback in the system.

Observed: Phase 3.5 budget generation died after ~190s with a raw
httpx.ReadTimeout escaping llm.chat_json. Because every layer of resilience here
catches LlmError only, one slow response bypassed all of them at once:

  * the 4-attempt retry loop in _post_chat (it only handled HTTP 429),
  * the Gemini -> OpenRouter -> Fireworks fallback chain in chat_json,
  * generate_proposal_budget's own "retry with compact output" fallback.

The phase hard-failed instead of degrading.
"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_generation_cancel import ProposalGenerationCancelled


class TransportErrorBecomesLlmErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patcher = mock.patch.object(llm.asyncio, "sleep", new=mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _post_chat(self):
        return await llm._post_chat(
            base_url="https://example.invalid",
            api_key="k",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            provider="TestProvider",
            max_tokens=128,
            temperature=0.2,
        )

    async def test_read_timeout_is_raised_as_llm_error(self) -> None:
        """The whole point: callers catching LlmError must now see it."""
        with mock.patch.object(
            llm.httpx.AsyncClient,
            "post",
            new=mock.AsyncMock(side_effect=httpx.ReadTimeout("timed out")),
        ):
            with self.assertRaises(LlmError) as ctx:
                await self._post_chat()

        self.assertIn("ReadTimeout", str(ctx.exception))

    async def test_connect_error_is_raised_as_llm_error(self) -> None:
        with mock.patch.object(
            llm.httpx.AsyncClient,
            "post",
            new=mock.AsyncMock(side_effect=httpx.ConnectError("refused")),
        ):
            with self.assertRaises(LlmError):
                await self._post_chat()

    async def test_transient_timeout_is_retried_then_succeeds(self) -> None:
        ok = httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"a":1}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
            request=httpx.Request("POST", "https://example.invalid"),
        )
        post = mock.AsyncMock(side_effect=[httpx.ReadTimeout("timed out"), ok])
        with mock.patch.object(llm.httpx.AsyncClient, "post", new=post):
            content, _usage = await self._post_chat()

        self.assertEqual(content, '{"a":1}')
        self.assertEqual(post.await_count, 2)

    async def test_transport_retries_are_bounded(self) -> None:
        """Each attempt can cost the full read timeout, so this must stay small."""
        post = mock.AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        with mock.patch.object(llm.httpx.AsyncClient, "post", new=post):
            with self.assertRaises(LlmError):
                await self._post_chat()

        self.assertEqual(post.await_count, llm._MAX_TRANSPORT_RETRIES + 1)

    async def test_stop_is_still_honoured_and_not_retried(self) -> None:
        """Cancellation must not be swallowed by the transport retry."""
        with mock.patch.object(
            llm,
            "_post_chat",
            new=llm._post_chat,
        ):
            with mock.patch(
                "app.services.proposal_generation_cancel.run_with_generation_cancel",
                new=mock.AsyncMock(side_effect=ProposalGenerationCancelled()),
            ):
                with self.assertRaises(ProposalGenerationCancelled):
                    await self._post_chat()


class GeminiTransportErrorTests(unittest.IsolatedAsyncioTestCase):
    """_post_gemini_chat had the same hole, and it broke the nightly briefs.

    A slow Gemini response raised httpx.ReadTimeout straight through
    chat_json's `except LlmError` Gemini block and through chat_json_soft,
    which promises never to raise. No fallback provider was ever tried.
    """

    async def _post_gemini(self):
        return await llm._post_gemini_chat(
            api_key="k",
            model="gemini-3.7-flash",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1400,
        )

    async def test_read_timeout_is_raised_as_llm_error(self) -> None:
        with mock.patch.object(
            llm.httpx.AsyncClient,
            "post",
            new=mock.AsyncMock(side_effect=httpx.ReadTimeout("timed out")),
        ):
            with self.assertRaises(LlmError) as ctx:
                await self._post_gemini()

        self.assertIn("ReadTimeout", str(ctx.exception))

    async def test_slow_gemini_falls_back_instead_of_escaping(self) -> None:
        """The behaviour the Teamwork brief actually needs: a usable answer."""
        ok = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"brief":"ok"}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
            request=httpx.Request("POST", "https://openrouter.invalid"),
        )

        async def _post(self_client, url, *args, **kwargs):
            if "generativelanguage" in str(url):
                raise httpx.ReadTimeout("timed out")
            return ok

        with mock.patch.object(llm.settings, "gemini_api_key", "gk"), mock.patch.object(
            llm.settings, "openrouter_api_key", "ok-key"
        ), mock.patch.object(llm.httpx.AsyncClient, "post", new=_post):
            payload, provider = await llm.chat_json_soft(
                [{"role": "user", "content": "hi"}],
                max_tokens=1400,
                tier="light",
                include_corrections=False,
            )

        self.assertEqual(provider, "openrouter")
        self.assertEqual(payload, {"brief": "ok"})


class ScaledTimeoutTests(unittest.TestCase):
    """Long-output calls were timing out because generation had not finished.

    The Stage 3 budget pass asks for 8192 tokens against a ~28k-char prompt on a
    heavy model; 180s was simply not enough wall-clock for it to return.
    """

    def test_budget_sized_call_gets_the_long_timeout(self) -> None:
        self.assertEqual(llm._http_timeout_for(8192), llm._HTTP_TIMEOUT_LONG_SECONDS)

    def test_ordinary_call_keeps_the_standard_timeout(self) -> None:
        self.assertEqual(llm._http_timeout_for(1200), llm._HTTP_TIMEOUT_SECONDS)
        self.assertEqual(llm._http_timeout_for(512), llm._HTTP_TIMEOUT_SECONDS)

    def test_unspecified_max_tokens_keeps_the_standard_timeout(self) -> None:
        self.assertEqual(llm._http_timeout_for(None), llm._HTTP_TIMEOUT_SECONDS)

    def test_threshold_boundary_is_inclusive(self) -> None:
        self.assertEqual(
            llm._http_timeout_for(llm._LONG_OUTPUT_TOKEN_THRESHOLD),
            llm._HTTP_TIMEOUT_LONG_SECONDS,
        )
        self.assertEqual(
            llm._http_timeout_for(llm._LONG_OUTPUT_TOKEN_THRESHOLD - 1),
            llm._HTTP_TIMEOUT_SECONDS,
        )

    def test_long_timeout_exceeds_the_observed_failure(self) -> None:
        """The budget call died at ~190s; the new ceiling must clear that."""
        self.assertGreater(llm._HTTP_TIMEOUT_LONG_SECONDS, 192.0)


if __name__ == "__main__":
    unittest.main()
