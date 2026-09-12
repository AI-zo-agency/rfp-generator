"""Financial AI nodes must not land in the proposal llm_call_log."""

from __future__ import annotations

from unittest.mock import patch

from app.services import llm
from app.services import monthly_llm_budget as budget


def test_financial_ai_insights_node_is_financial():
    assert llm._is_financial_node("financial.ai_insights")
    assert llm._is_financial_node("agency_insights")
    assert llm._is_financial_node("agency_chat.answer")


def test_record_successful_call_diverts_financial_nodes_away_from_proposal_ledger(
    monkeypatch,
):
    seen: list[dict] = []

    def fake_record_call(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(
        "app.financial.financial_llm_cost.record_call",
        fake_record_call,
    )
    with patch.object(llm, "_use_supabase", create=True):
        with patch("app.services.llm_call_log.record_llm_call") as proposal_ledger:
            llm._record_successful_call(
                model="google/gemini-3.6-flash",
                tier="light",
                provider="openrouter",
                usage={"prompt_tokens": 100, "completion_tokens": 20},
                latency_ms=50,
                node_name="financial.ai_insights",
                rfp_id="",
                run_id="unknown",
            )

    assert proposal_ledger.call_count == 0
    assert len(seen) == 1
    assert seen[0]["node_name"] == "financial.ai_insights"
    assert seen[0]["thread_id"].startswith("job:")


def test_monthly_budget_split_counts_misfiled_financial_nodes_as_finance(monkeypatch):
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-01T00:00:00+00:00"
    )
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    monkeypatch.setattr(
        budget,
        "_sum_llm_call_log_split",
        lambda *_a: (1.0, 0.0138),  # proposal, misfiled financial
    )
    monkeypatch.setattr(budget, "_sum_financial_llm_calls_usd", lambda *_a: 0.5)
    budget.clear_monthly_budget_cache()

    status = budget.get_monthly_budget_status(use_cache=False)
    assert status["proposal_spent_usd"] == 1.0
    assert status["financial_spent_usd"] == 0.5138
    assert status["spent_usd"] == 1.5138
