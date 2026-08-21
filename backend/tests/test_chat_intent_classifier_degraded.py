"""A provider outage must not silently turn every edit request into an essay.

`classify_chat_edit_intent` returned {"intent": "none"} on LlmError. At the
routing gate that clears `force_edit`, so routing fell back to the keyword gate,
whose documented safe default is advisory. The user asked for a rewrite, got
analysis, and nothing indicated the classifier had failed.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

if "langchain_openai" not in sys.modules:
    stub = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # noqa: D401
        pass

    stub.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = stub

from app.models.proposal import ProposalDraft, ProposalSection  # noqa: E402
from app.services import proposal_chat_manuscript_fix as mod  # noqa: E402
from app.services.llm import LlmError  # noqa: E402


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(
                id="s1", title="Approach", content="Prose.", required=True, custom=False
            )
        ],
        updatedAt="2026-01-01T00:00:00Z",
        generatedAt="2026-01-01T00:00:00Z",
    )


class IntentClassifierRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patcher = mock.patch.object(mod.asyncio, "sleep", new=mock.AsyncMock())
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    async def _classify(self):
        return await mod.classify_chat_edit_intent(
            user_message="check the compliance table and then rewrite it if anything is off", draft=_draft()
        )

    async def test_transient_failure_is_retried_and_succeeds(self) -> None:
        chat = mock.AsyncMock(
            side_effect=[
                LlmError("429 rate limited"),
                ({"intent": "single_edit", "primarySectionId": "s1"}, "openrouter"),
            ]
        )
        with mock.patch.object(mod.llm, "chat_json", new=chat):
            result = await self._classify()

        self.assertEqual(result["intent"], "single_edit")
        self.assertFalse(result.get("degraded"))
        self.assertEqual(chat.await_count, 2)

    async def test_persistent_failure_reports_degraded_not_a_confident_none(self) -> None:
        chat = mock.AsyncMock(side_effect=LlmError("402 no credit"))
        with mock.patch.object(mod.llm, "chat_json", new=chat):
            result = await self._classify()

        self.assertEqual(result["intent"], "none")
        self.assertTrue(result["degraded"], "outage must be distinguishable from 'none'")
        self.assertEqual(chat.await_count, mod._INTENT_CLASSIFY_RETRIES + 1)

    async def test_a_real_none_is_not_marked_degraded(self) -> None:
        """The model deciding 'none' must stay distinct from it failing to run."""
        chat = mock.AsyncMock(return_value=({"intent": "none"}, "openrouter"))
        with mock.patch.object(mod.llm, "chat_json", new=chat):
            result = await self._classify()

        self.assertEqual(result["intent"], "none")
        self.assertFalse(result.get("degraded"))
        self.assertEqual(chat.await_count, 1, "a real 'none' must not be retried")

    async def test_success_first_time_does_not_sleep(self) -> None:
        chat = mock.AsyncMock(return_value=({"intent": "advisory"}, "openrouter"))
        with mock.patch.object(mod.llm, "chat_json", new=chat):
            await self._classify()
        self.sleep.assert_not_awaited()

    async def test_retry_is_bounded(self) -> None:
        self.assertEqual(mod._INTENT_CLASSIFY_RETRIES, 1)


if __name__ == "__main__":
    unittest.main()
