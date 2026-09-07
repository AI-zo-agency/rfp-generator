"""HTTP contract tests for cached QuickBooks financial routes."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_sync_rejects_missing_secret(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_cron_secret",
        "abc",
    )
    response = client.post("/api/v1/financials/quickbooks/sync")
    assert response.status_code == 401


def test_sync_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_cron_secret",
        "abc",
    )
    response = client.post(
        "/api/v1/financials/quickbooks/sync",
        headers={"X-Cron-Secret": "nope"},
    )
    assert response.status_code == 401


def test_sync_rejects_when_configured_secret_is_empty(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_cron_secret",
        "",
    )
    response = client.post(
        "/api/v1/financials/quickbooks/sync",
        headers={"X-Cron-Secret": "anything"},
    )
    assert response.status_code == 401


def test_sync_lease_conflict_409(monkeypatch):
    from app.financial.qb_sync import LeaseHeld

    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_cron_secret",
        "abc",
    )
    monkeypatch.setattr(
        "app.financial.router.run_sync",
        lambda mode="auto": (_ for _ in ()).throw(LeaseHeld("busy")),
    )
    response = client.post(
        "/api/v1/financials/quickbooks/sync",
        headers={"X-Cron-Secret": "abc"},
    )
    assert response.status_code == 409


def test_refresh_runs_auto_sync_without_cron_secret(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "app.financial.router.run_sync",
        lambda mode="auto": calls.append(mode) or {"status": "success", "mode": "nightly", "run_id": "r1"},
    )
    response = client.post("/api/v1/financials/quickbooks/refresh")
    assert response.status_code == 200
    assert response.json()["mode"] == "nightly"
    assert calls == ["auto"]


def test_refresh_lease_conflict_409(monkeypatch):
    from app.financial.qb_sync import LeaseHeld

    monkeypatch.setattr(
        "app.financial.router.run_sync",
        lambda mode="auto": (_ for _ in ()).throw(LeaseHeld("busy")),
    )
    response = client.post("/api/v1/financials/quickbooks/refresh")
    assert response.status_code == 409


def test_overview_reads_cache_not_qbo(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_realm_id",
        "r1",
    )
    cache = {
        "payload": {"year": 2026, "ar": {"total": 1}, "errors": {}},
        "as_of": "2026-08-12",
        "computed_at": "2026-08-13T08:00:00+00:00",
    }
    monkeypatch.setattr(
        "app.financial.router.get_panel_cache",
        lambda realm, year: cache,
    )
    monkeypatch.setattr(
        "app.financial.router.get_sync_state",
        lambda realm: {
            "last_success_at": "2026-08-13T08:00:00+00:00",
            "last_error": None,
            "backfill_completed_at": "2026-08-13T08:00:00+00:00",
        },
    )
    with patch("app.financial.quickbooks.query") as query:
        response = client.get(
            "/api/v1/financials/quickbooks/overview"
            "?year=2026&refresh=true",
        )
    assert response.status_code == 200
    assert response.json()["as_of"] == "2026-08-12"
    assert response.json()["synced_at"] == "2026-08-13T08:00:00+00:00"
    assert response.json()["sync_status"] == "ok"
    query.assert_not_called()


def test_overview_recomputes_forecast_when_cache_lacks_it(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_realm_id",
        "r1",
    )
    cache = {
        "payload": {
            "year": 2026,
            "errors": {},
            "monthly_trend": {"months": [{"amount": 100}]},
        },
        "as_of": "2026-09-07",
        "computed_at": "2026-09-07T06:00:00+00:00",
    }
    monkeypatch.setattr(
        "app.financial.router.get_panel_cache",
        lambda realm, year: cache,
    )
    monkeypatch.setattr(
        "app.financial.router.get_sync_state",
        lambda realm: {
            "last_success_at": "2026-09-07T06:00:00+00:00",
            "last_error": None,
            "backfill_completed_at": "x",
        },
    )
    monkeypatch.setattr(
        "app.financial.router.build_forecast_panel",
        lambda realm_id, year, *, as_of, monthly_trend: {
            "as_of": as_of.isoformat(),
            "year": {"point": 1200},
            "quarter": None,
            "billing_gaps": {"clients": []},
        },
    )
    monkeypatch.setattr(
        "app.financial.router.get_latest_insight",
        lambda source, scope: (
            {
                "as_of": "2026-09-03",
                "model": "test-model",
                "payload": {"cash_13w": {"weeks": []}, "year": {"point": 1100}},
            }
            if source == "quickbooks_forecast"
            else {
                "as_of": "2026-09-07",
                "model": "test-model",
                "payload": {
                    "year": 2026,
                    "months": [{"month": "2026-09", "forecast": 90000, "actual": None}],
                    "mape": None,
                },
            }
        ),
    )

    response = client.get("/api/v1/financials/quickbooks/overview?year=2026")
    assert response.status_code == 200
    forecast = response.json()["forecast"]
    assert forecast["year"]["point"] == 1200
    assert forecast["llm"]["year"]["point"] == 1100
    assert forecast["llm_as_of"] == "2026-09-03"
    assert forecast["monthly"]["months"][0]["forecast"] == 90000


def test_overview_past_year_omits_live_cash_forecast(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_realm_id",
        "r1",
    )
    cache = {
        "payload": {
            "year": 2025,
            "errors": {},
            "forecast": {
                "as_of": "2025-12-31",
                "year": {"point": 1_200_000},
                "quarter": None,
                "billing_gaps": {"clients": []},
            },
        },
        "as_of": "2025-12-31",
        "computed_at": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(
        "app.financial.router.get_panel_cache",
        lambda realm, year: cache,
    )
    monkeypatch.setattr(
        "app.financial.router.get_sync_state",
        lambda realm: {
            "last_success_at": "2026-01-01T00:00:00+00:00",
            "last_error": None,
            "backfill_completed_at": "x",
        },
    )
    monkeypatch.setattr(
        "app.financial.router.get_latest_insight",
        lambda source, scope: (
            {
                "as_of": "2026-09-07",
                "model": "live",
                "payload": {
                    "cash_13w": {"weeks": [{"week": 1}], "trough": {"amount": 1, "week": 1}},
                    "year": {"point": 999},
                },
            }
            if source == "quickbooks_forecast"
            else {
                "as_of": "2026-01-02",
                "model": "test-model",
                "payload": {
                    "year": 2025,
                    "months": [
                        {
                            "month": "2025-03",
                            "forecast": 94500,
                            "actual": 132944,
                            "error_pct": 28.9,
                        }
                    ],
                    "mape": 22.0,
                },
            }
        ),
    )

    response = client.get("/api/v1/financials/quickbooks/overview?year=2025")
    assert response.status_code == 200
    forecast = response.json()["forecast"]
    assert forecast["llm"] is None
    assert forecast["monthly"]["mape"] == 22.0
    assert forecast["monthly"]["months"][0]["actual"] == 132944


def test_overview_missing_cache_returns_null_panels(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_realm_id",
        "r1",
    )
    monkeypatch.setattr(
        "app.financial.router.get_panel_cache",
        lambda realm, year: None,
    )
    monkeypatch.setattr(
        "app.financial.router.get_sync_state",
        lambda realm: {"backfill_completed_at": None},
    )
    response = client.get(
        "/api/v1/financials/quickbooks/overview?year=2026",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["year"] == 2026
    assert body["ar"] is None
    assert body["errors"] == {"overview": "no snapshot for year"}
    assert body["sync_status"] == "backfill_pending"


def test_status_includes_sync_fields(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.quickbooks_oauth.connection_status",
        lambda: {
            "connected": True,
            "realm_id": "r1",
            "environment": "sandbox",
            "refresh_token_days_remaining": 90,
        },
    )
    monkeypatch.setattr(
        "app.financial.router.get_sync_state",
        lambda realm: {
            "last_success_at": "2026-08-13T08:00:00+00:00",
            "last_error": None,
            "backfill_completed_at": "x",
        },
    )
    monkeypatch.setattr(
        "app.financial.router.settings.quickbooks_realm_id",
        "r1",
    )
    response = client.get("/api/v1/financials/quickbooks/status")
    assert response.status_code == 200
    assert response.json()["backfill_completed"] is True
    assert response.json()["last_success_at"] == "2026-08-13T08:00:00+00:00"
    assert response.json()["last_error"] is None
