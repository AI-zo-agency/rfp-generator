"""A transient provider error must not permanently kill a section.

Phase 3 previously wrote SECTION_DRAFT_FAILURE_PLACEHOLDER after a single failed
single-section call, with no backoff. One rate-limit blip converted a section into
a placeholder that only chat could recover. One bounded retry removes most of
those at the source.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.services import proposal_drafting_graph as graph
from app.services.llm import LlmError


def _section(sid: str = "s15") -> dict:
    return {"id": sid, "title": "Standard Contract Acknowledgment"}


def _drafted(sid: str = "s15") -> list[dict]:
    return [{"id": sid, "title": "Standard Contract Acknowledgment", "content": "Prose."}]


class SingleSectionRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Keep the test fast; the backoff itself is not what we are asserting.
        patcher = mock.patch.object(graph.asyncio, "sleep", new=mock.AsyncMock())
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_transient_failure_is_retried_and_succeeds(self) -> None:
        once = mock.AsyncMock(side_effect=[LlmError("429 rate limited"), (_drafted(), "openrouter")])
        with mock.patch.object(graph, "_draft_batch_once", new=once):
            results, provider = await graph._draft_single_with_retry(_section(), {})

        self.assertEqual(once.await_count, 2)
        self.assertEqual(provider, "openrouter")
        self.assertEqual(results[0]["content"], "Prose.")
        self.sleep.assert_awaited_once()

    async def test_persistent_failure_still_raises(self) -> None:
        always = mock.AsyncMock(side_effect=LlmError("402 no credit"))
        with mock.patch.object(graph, "_draft_batch_once", new=always):
            with self.assertRaises(LlmError):
                await graph._draft_single_with_retry(_section(), {})

        # Original attempt plus the bounded retry, and no more.
        self.assertEqual(always.await_count, graph._SINGLE_SECTION_RETRIES + 1)

    async def test_success_first_time_does_not_sleep(self) -> None:
        first = mock.AsyncMock(return_value=(_drafted(), "openrouter"))
        with mock.patch.object(graph, "_draft_batch_once", new=first):
            await graph._draft_single_with_retry(_section(), {})

        self.assertEqual(first.await_count, 1)
        self.sleep.assert_not_awaited()

    async def test_retry_is_bounded(self) -> None:
        """Worst-case Phase 3 latency must stay predictable."""
        self.assertEqual(graph._SINGLE_SECTION_RETRIES, 1)


if __name__ == "__main__":
    unittest.main()
