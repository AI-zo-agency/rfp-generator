"""chat_text refuses provider calls when the monthly org budget is exhausted."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services import llm
from app.services.llm import LlmError


def test_chat_text_blocks_before_provider_when_monthly_budget_hit(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_api_key", "AIzaSyRealLookingKey123456")
    monkeypatch.setattr(llm.settings, "llm_prefer_openrouter", False)
    monkeypatch.setattr(llm.settings, "llm_prefer_fireworks", False)

    def boom() -> None:
        raise LlmError("Monthly LLM budget exceeded", status_code=429)

    monkeypatch.setattr(llm, "_enforce_monthly_llm_budget", boom)
    gemini = AsyncMock(return_value=("hello", {"prompt_tokens": 1, "completion_tokens": 1}))
    with patch.object(llm, "_post_gemini_chat", gemini):
        with pytest.raises(LlmError) as ctx:
            asyncio.run(llm.chat_text([{"role": "user", "content": "hi"}]))

    assert ctx.value.status_code == 429
    assert gemini.await_count == 0


def test_chat_json_blocks_before_provider_when_monthly_budget_hit(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_api_key", "AIzaSyRealLookingKey123456")
    monkeypatch.setattr(llm.settings, "llm_prefer_openrouter", False)
    monkeypatch.setattr(llm.settings, "llm_prefer_fireworks", False)

    def boom() -> None:
        raise LlmError("Monthly LLM budget exceeded", status_code=429)

    monkeypatch.setattr(llm, "_enforce_monthly_llm_budget", boom)
    gemini = AsyncMock(return_value=('{"ok":true}', {"prompt_tokens": 1, "completion_tokens": 1}))
    with patch.object(llm, "_post_gemini_chat", gemini):
        with pytest.raises(LlmError) as ctx:
            asyncio.run(llm.chat_json([{"role": "user", "content": "hi"}]))

    assert ctx.value.status_code == 429
    assert gemini.await_count == 0
