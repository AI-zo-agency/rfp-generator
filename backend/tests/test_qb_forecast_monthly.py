"""Unit tests for monthly revenue forecast helpers."""

from __future__ import annotations

import asyncio

import pytest

from app.financial import qb_forecast_monthly as M


def test_history_window_is_exactly_ten_prior_months():
    series = [
        {"label": f"m{i}", "year": 2023 + (i // 12), "month": (i % 12) + 1, "income": float(i)}
        for i in range(24)
    ]
    # Index 13 → need series[3:13]
    window = M.history_window(series, 13)
    assert len(window) == 10
    assert window[0]["label"] == "m3"
    assert window[-1]["label"] == "m12"


def test_history_window_rejects_thin_history():
    series = [{"label": "Jan 2024", "year": 2024, "month": 1, "income": 1.0}]
    with pytest.raises(ValueError, match="need 10"):
        M.history_window(series, 0)


def test_trail3_mean():
    history = [
        {"income": 10.0},
        {"income": 20.0},
        {"income": 30.0},
        {"income": 40.0},
    ]
    assert M.trail3_mean(history) == 30.0


def test_error_pct():
    assert M._error_pct(100.0, 80.0) == 20.0
    assert M._error_pct(None, 80.0) is None
    assert M._error_pct(0.0, 80.0) is None


def test_scope_key():
    assert M.scope_key("realm-1", 2025) == "realm-1:2025"


def test_as_of_for_target():
    assert M._as_of_for_target(2025, 1) == "2024-12-31"
    assert M._as_of_for_target(2025, 3) == "2025-02-28"


def test_backfill_year_persists_scored_months(monkeypatch):
    series = []
    for year, base in ((2024, 0), (2025, 12)):
        for month in range(1, 13):
            series.append(
                {
                    "label": M._label(year, month),
                    "year": year,
                    "month": month,
                    "income": 1000.0 * (base + month),
                }
            )
    monkeypatch.setattr(M, "load_income_series", lambda realm, year: series)

    async def fake_predict(history, label):
        return {
            "point": 5000.0,
            "low": 4000.0,
            "high": 6000.0,
            "confidence": "low",
            "reasoning": "test",
            "provider": "test",
            "baseline_trail3": M.trail3_mean(history),
        }

    monkeypatch.setattr(M, "forecast_one_month", fake_predict)
    stored: list[tuple] = []

    def fake_store(realm_id, year, months):
        stored.append((realm_id, year, months))

    monkeypatch.setattr(M, "_store", fake_store)
    monkeypatch.setattr(
        M,
        "get_latest_insight",
        lambda source, scope: {
            "payload": {"mape": 12.5, "months": stored[-1][2] if stored else []}
        },
    )

    result = asyncio.run(M.backfill_year("r1", 2025))
    assert result["status"] == "ok"
    assert result["months"] == 12
    assert stored[0][0] == "r1"
    assert stored[0][1] == 2025
    march = next(m for m in stored[0][2] if m["month"] == "2025-03")
    assert march["forecast"] == 5000.0
    assert march["actual"] == 15000.0  # 1000 * (12+3)
    assert march["baseline_trail3"] is not None


def test_refresh_locks_closed_and_reforecasts_open(monkeypatch):
    series = []
    for year in (2025, 2026):
        for month in range(1, 13):
            series.append(
                {
                    "label": M._label(year, month),
                    "year": year,
                    "month": month,
                    "income": 10_000.0 if year == 2026 and month <= 8 else 0.0,
                }
            )
    monkeypatch.setattr(M, "load_income_series", lambda realm, year: series)
    monkeypatch.setattr(
        M,
        "_existing_months",
        lambda realm, year: {
            "2026-01": {
                "month": "2026-01",
                "label": "Jan 2026",
                "forecast": 9000.0,
                "actual": None,
                "as_of": "2025-12-31",
                "method": "llm",
                "baseline_trail3": 8000.0,
            }
        },
    )

    calls: list[str] = []

    async def fake_predict(series_arg, year, month):
        calls.append(M._ym(year, month))
        return {
            "month": M._ym(year, month),
            "label": M._label(year, month),
            "forecast": 111.0,
            "low": 100.0,
            "high": 120.0,
            "actual": None,
            "error_pct": None,
            "as_of": M._as_of_for_target(year, month),
            "method": "llm",
            "baseline_trail3": 100.0,
            "confidence": "low",
            "reasoning": None,
        }

    monkeypatch.setattr(M, "_predict_month", fake_predict)
    stored: list = []
    monkeypatch.setattr(M, "_store", lambda *a, **k: stored.append(a))

    result = asyncio.run(M.refresh_current_year("r1", 2026, "2026-09-07"))
    assert result["status"] == "ok"
    assert result["locked"] == 1  # Jan kept from existing
    assert "2026-01" not in calls
    assert "2026-09" in calls
    assert "2026-12" in calls
    jan = next(m for m in stored[0][2] if m["month"] == "2026-01")
    assert jan["forecast"] == 9000.0
    assert jan["actual"] == 10_000.0
