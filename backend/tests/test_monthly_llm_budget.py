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
        # Month window is epoch-clipped to Sep 12; week is Mon Sep 14; day is Sep 15.
        if start.day == 12:
            return (7.5, 2.25, 9.75, [])
        if start.day == 15:
            return (0.4, 0.1, 0.5, [])
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
    assert status["week_outreach_spent_usd"] == 0.0
    assert status["outreach_spent_usd"] == 0.0
    assert status["day_limit_usd"] == pytest.approx(5.0)
    assert status["day_spent_usd"] == pytest.approx(0.5)
    assert status["day_proposal_spent_usd"] == pytest.approx(0.4)
    assert status["day_financial_spent_usd"] == pytest.approx(0.1)
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
    assert len(starts) == 3
    # Month window starts Sep 1 → clipped to epoch; week starts Mon Sep 14 (> epoch);
    # day starts Sep 15.
    assert starts[0].startswith("2026-09-12T05:00:00")
    assert starts[1].startswith("2026-09-14")
    assert starts[2].startswith("2026-09-15")


def test_week_window_is_monday_utc():
    now = budget.datetime(2026, 9, 15, 12, 0, tzinfo=budget.timezone.utc)  # Tuesday
    start, end = budget._week_window(now)
    assert start.weekday() == 0  # Monday
    assert start.day == 14
    assert (end - start).days == 7


def test_day_window_is_utc_calendar_day():
    now = budget.datetime(2026, 9, 15, 18, 30, tzinfo=budget.timezone.utc)
    start, end = budget._day_window(now)
    assert start.hour == 0
    assert start.day == 15
    assert (end - start).days == 1


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


def test_spend_bucket_splits_ralph_finance_and_outreach():
    assert budget._spend_bucket("financial.ai_insights") == "financial"
    assert budget._spend_bucket("leads_enrich") == "outreach"
    assert budget._spend_bucket("leads_brief") == "outreach"
    assert budget._spend_bucket("opportunity_extract") == "proposal"


def test_prior_weeks_skip_the_open_week_and_zero_weeks(monkeypatch):
    budget.clear_monthly_budget_cache()
    monkeypatch.setattr(
        budget.settings, "monthly_llm_budget_epoch", "2026-01-01T00:00:00+00:00"
    )
    monkeypatch.setattr(
        budget,
        "_utcnow",
        lambda: budget.datetime(2026, 9, 15, 12, 0, tzinfo=budget.timezone.utc),
    )
    seen: list[int] = []

    def fake_spend(start, end):
        seen.append(start.day)
        # Only the week starting Sep 7 has spend. Open week (Sep 14) is not queried.
        if start.day == 7:
            return (10.0, 0.4, 10.5, [], 0.1)
        return (0.0, 0.0, 0.0, [], 0.0)

    monkeypatch.setattr(budget, "_period_spend", fake_spend)
    weeks = budget.list_prior_weeks(count=3)
    assert seen[0] == 7
    assert 14 not in seen
    assert len(weeks) == 1
    assert weeks[0]["label"] == "Sep 7–13"
    assert weeks[0]["spent_usd"] == pytest.approx(10.5)
    assert weeks[0]["proposal_spent_usd"] == pytest.approx(10.0)
    assert weeks[0]["financial_spent_usd"] == pytest.approx(0.4)
    assert weeks[0]["outreach_spent_usd"] == pytest.approx(0.1)
