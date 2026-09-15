"""Hard blocks: no provider calls for demo/test RFPs or anonymous proposal LLM."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services import llm
from app.services.llm import LlmError
from app.services.llm_call_context import llm_call_context


def test_blocked_rfp_id_patterns():
    from app.services.llm_call_guards import is_blocked_rfp_id

    assert is_blocked_rfp_id("demo-6641484746ed") is True
    assert is_blocked_rfp_id("DEMO-abc") is True
    assert is_blocked_rfp_id("fixture-cvvb-v2-rfp") is True
    assert is_blocked_rfp_id("rfp-test") is True
    assert is_blocked_rfp_id("rfp-test-2") is True
    assert is_blocked_rfp_id("rfp-jw-1791e105-16a4-433a-bdb9-97d82c409f50") is False
    assert is_blocked_rfp_id("") is False


def test_enforce_blocks_demo_rfp(monkeypatch):
    from app.services import llm_call_guards as guards

    monkeypatch.setattr(guards.settings, "llm_block_ephemeral_rfp_ids", True)
    monkeypatch.setattr(guards.settings, "llm_require_user_email_for_proposals", False)

    with llm_call_context(rfp_id="demo-e02f6d7a1f5e", node_name="opportunity_extract"):
        with pytest.raises(LlmError) as ctx:
            guards.enforce_llm_call_guards()
    assert ctx.value.status_code == 403
    assert "demo" in str(ctx.value).lower() or "ephemeral" in str(ctx.value).lower()


def test_enforce_blocks_anonymous_proposal_llm(monkeypatch):
    from app.services import llm_call_guards as guards

    monkeypatch.setattr(guards.settings, "llm_block_ephemeral_rfp_ids", True)
    monkeypatch.setattr(guards.settings, "llm_require_user_email_for_proposals", True)

    with llm_call_context(
        rfp_id="rfp-jw-1791e105-16a4-433a-bdb9-97d82c409f50",
        node_name="phase-2",
        user_email="",
    ):
        with pytest.raises(LlmError) as ctx:
            guards.enforce_llm_call_guards()
    assert ctx.value.status_code == 403
    assert "email" in str(ctx.value).lower()


def test_enforce_allows_signed_in_real_rfp(monkeypatch):
    from app.services import llm_call_guards as guards

    monkeypatch.setattr(guards.settings, "llm_block_ephemeral_rfp_ids", True)
    monkeypatch.setattr(guards.settings, "llm_require_user_email_for_proposals", True)

    with llm_call_context(
        rfp_id="rfp-jw-1791e105-16a4-433a-bdb9-97d82c409f50",
        node_name="phase-2",
        user_email="dev@zo.com",
    ):
        guards.enforce_llm_call_guards()  # must not raise


def test_enforce_skips_email_rule_for_financial_nodes(monkeypatch):
    from app.services import llm_call_guards as guards

    monkeypatch.setattr(guards.settings, "llm_require_user_email_for_proposals", True)
    with llm_call_context(rfp_id="", node_name="financial.ai_insights", user_email=""):
        guards.enforce_llm_call_guards()


def test_chat_text_never_hits_provider_for_demo_rfp(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_api_key", "AIzaSyRealLookingKey123456")
    monkeypatch.setattr(llm.settings, "llm_prefer_openrouter", False)
    monkeypatch.setattr(llm.settings, "llm_prefer_fireworks", False)
    monkeypatch.setattr(llm.settings, "llm_block_ephemeral_rfp_ids", True)
    monkeypatch.setattr(llm.settings, "llm_require_user_email_for_proposals", False)
    monkeypatch.setattr(llm, "_enforce_monthly_llm_budget", lambda: None)

    gemini = AsyncMock(return_value=("hello", {"prompt_tokens": 1, "completion_tokens": 1}))
    with patch.object(llm, "_post_gemini_chat", gemini):
        with llm_call_context(rfp_id="demo-6641484746ed", node_name="opportunity_extract"):
            with pytest.raises(LlmError) as ctx:
                asyncio.run(llm.chat_text([{"role": "user", "content": "hi"}]))

    assert ctx.value.status_code == 403
    assert gemini.await_count == 0
