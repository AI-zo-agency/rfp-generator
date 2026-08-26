"""Financial workspace LLM calls use OPENROUTER_API_KEY_FINANCIAL + model."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services import llm


def test_financial_node_uses_financial_openrouter_key_and_model(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_post(*, api_key: str, model: str, **_kwargs):
        captured["api_key"] = api_key
        captured["model"] = model
        return "ok", {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(llm.settings, "gemini_api_key", "AIzaSyRealLookingKey123456")
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "proposal-key")
    monkeypatch.setattr(llm.settings, "openrouter_api_key_financial", "financial-key")
    monkeypatch.setattr(llm.settings, "openrouter_model_financial", "google/gemini-2.5-flash")
    monkeypatch.setattr(llm.settings, "llm_prefer_openrouter", False)
    monkeypatch.setattr(llm.settings, "llm_prefer_fireworks", False)
    monkeypatch.setattr(llm.settings, "llm_disable_fireworks", True)

    with patch.object(llm, "_post_chat", fake_post), patch.object(
        llm, "_post_gemini_chat", AsyncMock(side_effect=AssertionError("gemini must not run"))
    ):
        raw, provider = asyncio.run(
            llm.chat_text(
                [{"role": "user", "content": "hi"}],
                node_name="teamwork_insights",
                include_corrections=False,
                cost_sink=lambda **_kw: None,
            )
        )

    assert (raw, provider) == ("ok", "openrouter")
    assert captured["api_key"] == "financial-key"
    assert captured["model"] == "google/gemini-2.5-flash"


def test_proposal_node_keeps_the_shared_openrouter_key(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_post(*, api_key: str, model: str, **_kwargs):
        captured["api_key"] = api_key
        captured["model"] = model
        return "ok", {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "proposal-key")
    monkeypatch.setattr(llm.settings, "openrouter_api_key_financial", "financial-key")
    monkeypatch.setattr(llm.settings, "openrouter_model", "anthropic/claude-sonnet-4")
    monkeypatch.setattr(llm.settings, "llm_heavy_model", "anthropic/claude-sonnet-4")
    monkeypatch.setattr(llm.settings, "llm_prefer_openrouter", True)
    monkeypatch.setattr(llm.settings, "llm_prefer_fireworks", False)
    monkeypatch.setattr(llm.settings, "llm_disable_fireworks", True)

    with patch.object(llm, "_post_chat", fake_post):
        asyncio.run(
            llm.chat_text(
                [{"role": "user", "content": "hi"}],
                node_name="proposal_generator",
                include_corrections=False,
            )
        )

    assert captured["api_key"] == "proposal-key"
    assert captured["model"] == "anthropic/claude-sonnet-4"
