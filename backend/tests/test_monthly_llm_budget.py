"""Hard monthly LLM budget — covers proposals + finance ledgers."""

from __future__ import annotations

import pytest

from app.services import monthly_llm_budget as budget
from app.services.llm import LlmError


def test_status_sums_proposal_and_finance_after_epoch(monkeypatch):
    budget.clear_monthly_budget_cache()
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-12T00:00:00+00:00"
    )
    monkeypatch.setattr(
        budget,
        "_sum_llm_call_log_split",
        lambda start, end: (7.5, 0.0),
    )
    monkeypatch.setattr(
        budget,
        "_sum_financial_llm_calls_usd",
        lambda start, end: 2.25,
    )
    # Freeze "now" inside September so month window is Sep 1–Oct 1.
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, 12, 0, tzinfo=budget.timezone.utc),
    )

    status = budget.get_monthly_budget_status()

    assert status["limit_usd"] == 20.0
    assert status["spent_usd"] == pytest.approx(9.75)
    assert status["remaining_usd"] == pytest.approx(10.25)
    assert status["blocked"] is False
    assert status["proposal_spent_usd"] == pytest.approx(7.5)
    assert status["financial_spent_usd"] == pytest.approx(2.25)
    assert status["period_start"].startswith("2026-09-12")  # epoch clips month start


def test_enforce_blocks_when_spent_at_or_over_limit(monkeypatch):
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-01T00:00:00+00:00"
    )
    monkeypatch.setattr(budget, "_sum_llm_call_log_split", lambda *_a: (18.0, 0.0))
    monkeypatch.setattr(budget, "_sum_financial_llm_calls_usd", lambda *_a: 2.0)
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    budget.clear_monthly_budget_cache()

    with pytest.raises(LlmError) as ctx:
        budget.enforce_monthly_llm_budget()
    assert ctx.value.status_code == 429
    assert "monthly" in str(ctx.value).lower()


def test_enforce_allows_when_under_limit(monkeypatch):
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-01T00:00:00+00:00"
    )
    monkeypatch.setattr(budget, "_sum_llm_call_log_split", lambda *_a: (1.0, 0.0))
    monkeypatch.setattr(budget, "_sum_financial_llm_calls_usd", lambda *_a: 0.5)
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    budget.clear_monthly_budget_cache()
    budget.enforce_monthly_llm_budget()  # must not raise


def test_zero_limit_disables_guard(monkeypatch):
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 0.0)
    monkeypatch.setattr(budget, "_sum_llm_call_log_split", lambda *_a: (999.0, 0.0))
    monkeypatch.setattr(budget, "_sum_financial_llm_calls_usd", lambda *_a: 999.0)
    budget.clear_monthly_budget_cache()
    budget.enforce_monthly_llm_budget()
    status = budget.get_monthly_budget_status()
    assert status["blocked"] is False
    assert status["enabled"] is False


def test_pre_epoch_spend_ignored(monkeypatch):
    """Only rows after epoch count — old history does not burn the $20."""
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-12T05:00:00+00:00"
    )
    calls: list[tuple[str, str]] = []

    def capture_split(start: str, end: str) -> tuple[float, float]:
        calls.append(("proposal", start))
        return 0.0, 0.0

    def capture_fin(start: str, end: str) -> float:
        calls.append(("fin", start))
        return 0.0

    monkeypatch.setattr(budget, "_sum_llm_call_log_split", capture_split)
    monkeypatch.setattr(budget, "_sum_financial_llm_calls_usd", capture_fin)
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    budget.clear_monthly_budget_cache()
    budget.get_monthly_budget_status()
    assert calls
    assert all(start.startswith("2026-09-12T05:00:00") for _, start in calls)
