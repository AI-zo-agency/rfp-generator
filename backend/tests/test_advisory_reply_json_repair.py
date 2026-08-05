"""Advisory chat replies must survive a malformed-JSON response.

Observed live: asking "does this section meet the RFP?" returned HTTP 502 on
roughly half of attempts. The reply itself generated fine (1,639 chars) — it was
long markdown inside {"reply": "..."} that the strict parser rejected, and the
chat path had no repair pass, unlike Phase 3 drafting.
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

from app.services import proposal_draft_llm as repair_mod  # noqa: E402
from app.services.llm import LlmError  # noqa: E402


class AdvisoryReplyJsonRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_failure_is_repaired_rather_than_raised(self) -> None:
        chat = mock.AsyncMock(
            side_effect=[
                LlmError("LLM returned invalid JSON: {\"reply\": \"**Section 1.1"),
                ({"reply": "Section 1.1 partially meets the RFP."}, "openrouter"),
            ]
        )
        with mock.patch.object(repair_mod.llm, "chat_json", new=chat):
            raw, _provider = await repair_mod.chat_json_with_repair(
                [{"role": "user", "content": "does this meet the RFP?"}],
                max_tokens=1200,
                temperature=0.35,
            )

        self.assertEqual(raw["reply"], "Section 1.1 partially meets the RFP.")
        self.assertEqual(chat.await_count, 2, "should retry once with a repair prompt")

    async def test_repair_prompt_demands_bare_json(self) -> None:
        chat = mock.AsyncMock(
            side_effect=[LlmError("invalid JSON"), ({"reply": "ok"}, "openrouter")]
        )
        with mock.patch.object(repair_mod.llm, "chat_json", new=chat):
            await repair_mod.chat_json_with_repair([{"role": "user", "content": "q"}])

        repair_messages = chat.await_args_list[1].args[0]
        self.assertIn("not valid JSON", repair_messages[-1]["content"])

    async def test_non_json_errors_are_not_swallowed(self) -> None:
        """A rate limit is not a parse failure and must surface, not silently retry."""
        chat = mock.AsyncMock(side_effect=LlmError("429 upstream rate limit"))
        with mock.patch.object(repair_mod.llm, "chat_json", new=chat):
            with self.assertRaises(LlmError):
                await repair_mod.chat_json_with_repair([{"role": "user", "content": "q"}])

    async def test_advisory_path_uses_the_repair_helper(self) -> None:
        """Guard against the chat path silently reverting to bare llm.chat_json."""
        import inspect

        from app.services import proposal_section_editor as editor

        source = inspect.getsource(editor._section_chat_advisory_reply)
        self.assertIn("chat_json_with_repair", source)
        self.assertNotIn("await llm.chat_json(", source)


if __name__ == "__main__":
    unittest.main()
