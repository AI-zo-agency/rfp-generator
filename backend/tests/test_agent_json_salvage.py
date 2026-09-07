"""Tests for agent JSON salvage — never send empty messages to the LLM."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.llm import LlmError
from app.services.proposal_langchain_agents import _parse_json_from_agent_text


class AgentJsonSalvageTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_text_skips_llm_salvage(self) -> None:
        with patch(
            "app.services.proposal_langchain_agents.chat_json",
            new_callable=AsyncMock,
        ) as mock_chat:
            out = await _parse_json_from_agent_text("")
            self.assertEqual(out, {})
            mock_chat.assert_not_awaited()

    async def test_whitespace_skips_llm_salvage(self) -> None:
        with patch(
            "app.services.proposal_langchain_agents.chat_json",
            new_callable=AsyncMock,
        ) as mock_chat:
            out = await _parse_json_from_agent_text("   \n\t  ")
            self.assertEqual(out, {})
            mock_chat.assert_not_awaited()

    async def test_local_content_key_salvage_before_llm(self) -> None:
        raw = '{"content": "We will run monthly conversion checks on ticket flows.", "kbRefs": []'
        with patch(
            "app.services.proposal_langchain_agents.chat_json",
            new_callable=AsyncMock,
        ) as mock_chat:
            out = await _parse_json_from_agent_text(raw)
            self.assertIn("monthly conversion", out.get("content", ""))
            mock_chat.assert_not_awaited()

    async def test_llm_salvage_failure_returns_empty_not_raise(self) -> None:
        broken = "{not-json-and-no-content-key " + ("x" * 80)
        with patch(
            "app.services.proposal_langchain_agents.chat_json",
            new_callable=AsyncMock,
            side_effect=LlmError("messages: at least one message is required", status_code=400),
        ) as mock_chat:
            out = await _parse_json_from_agent_text(broken)
            self.assertEqual(out, {})
            mock_chat.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
