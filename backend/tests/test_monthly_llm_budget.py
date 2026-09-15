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

    def fake_spend(start, end):
        # Month window is epoch-clipped to Sep 12; week is Mon Sep 14.
        if start.day == 12:
            return (7.5, 2.25, 9.75, [])
        return (1.0, 0.5, 1.5, [])

    monkeypatch.setattr(budget, "_period_spend", fake_spend)
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
    assert status["week_spent_usd"] == pytest.approx(1.5)
    assert status["week_proposal_spent_usd"] == pytest.approx(1.0)
    assert status["week_financial_spent_usd"] == pytest.approx(0.5)
    assert status["period_start"].startswith("2026-09-12")


def test_enforce_blocks_when_spent_at_or_over_limit(monkeypatch):
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-01T00:00:00+00:00"
    )
    monkeypatch.setattr(budget, "_period_spend", lambda *_a: (18.0, 2.0, 20.0, []))
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
    monkeypatch.setattr(budget, "_period_spend", lambda *_a: (1.0, 0.5, 1.5, []))
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    budget.clear_monthly_budget_cache()
    budget.enforce_monthly_llm_budget()  # must not raise


def test_zero_limit_disables_guard(monkeypatch):
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 0.0)
    monkeypatch.setattr(budget, "_period_spend", lambda *_a: (999.0, 999.0, 1998.0, []))
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
    starts: list[str] = []

    def capture_spend(start, end):
        starts.append(budget._iso(start))
        return 0.0, 0.0, 0.0, []

    monkeypatch.setattr(budget, "_period_spend", capture_spend)
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    budget.clear_monthly_budget_cache()
    budget.get_monthly_budget_status()
    assert len(starts) == 2
    # Month window starts Sep 1 → clipped to epoch; week starts Mon Sep 14 (> epoch).
    assert starts[0].startswith("2026-09-12T05:00:00")
    assert starts[1].startswith("2026-09-14")


def test_week_window_is_monday_utc():
    now = budget.datetime(2026, 9, 15, 12, 0, tzinfo=budget.timezone.utc)  # Tuesday
    start, end = budget._week_window(now)
    assert start.weekday() == 0  # Monday
    assert start.day == 14
    assert (end - start).days == 7


def test_status_includes_proposal_by_user(monkeypatch):
    budget.clear_monthly_budget_cache()
    monkeypatch.setattr(budget.settings, "monthly_llm_budget_usd", 20.0)
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-09-12T00:00:00+00:00"
    )
    users = [
        {"email": "a@zo.com", "proposal_spent_usd": 2.0},
        {"email": "b@zo.com", "proposal_spent_usd": 1.0},
    ]
    monkeypatch.setattr(
        budget, "_period_spend", lambda *_a: (3.0, 0.0, 3.0, users)
    )
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, tzinfo=budget.timezone.utc),
    )
    status = budget.get_monthly_budget_status()
    assert status["proposal_by_user"][0]["email"] == "a@zo.com"
    assert status["week_proposal_by_user"][0]["proposal_spent_usd"] == 2.0
