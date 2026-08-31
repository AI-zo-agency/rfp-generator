"""HTTP wiring for Agency weekly intelligence."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app.financial import router as fin_router
from app.main import app

client = TestClient(app)


def _overview() -> dict:
    return {
        "as_of": "2026-09-01",
        "position": {"join_mapped": 2, "join_total": 3, "open_ar": 1000, "live_jobs": 2},
        "jobs": [],
        "unlinked_invoices": [],
        "billed_without_project": [],
    }


def test_agency_ai_insights_returns_weekly_shape(monkeypatch):
    monkeypatch.setattr(fin_router, "build_agency_overview", _overview)
    monkeypatch.setattr(fin_router, "_agency_insight_row", lambda: None)
    monkeypatch.setattr(fin_router, "_agency_carryover_baseline", lambda _row=None: None)

    response = client.get("/api/v1/financials/agency/ai-insights")

    assert response.status_code == 200
    body = response.json()
    for key in (
        "status",
        "brief",
        "notes",
        "signals",
        "cadence",
        "period_label",
        "current_week_label",
        "bootstrap",
        "as_of",
        "generated_at",
        "provider",
        "model",
        "stale",
    ):
        assert key in body
    assert body["cadence"] == "weekly"
    assert body["status"] == "empty"
    assert body["bootstrap"] is True


def test_agency_regenerate_delegates_to_generate(monkeypatch):
    row = {
        "as_of": "2026-08-25",
        "generated_at": "2026-08-31T06:00:00Z",
        "provider": "gemini",
        "payload": {"brief": "Focus on carryover.", "notes": {}, "period_label": "Monday 25 August to Friday 29 August"},
        "evidence": {"open_items": [{"id": "delivery:1"}]},
    }
    monkeypatch.setattr(fin_router, "build_agency_overview", _overview)
    generate = Mock(return_value="ok")
    monkeypatch.setattr(fin_router, "generate_agency_insight", generate)
    monkeypatch.setattr(fin_router, "_agency_insight_row", lambda: row)
    monkeypatch.setattr(fin_router, "_agency_carryover_baseline", lambda _row=None: row["evidence"])

    response = client.post("/api/v1/financials/agency/ai-insights/regenerate")

    assert response.status_code == 200
    assert generate.called
    assert response.json()["generated"] == "ok"
    assert response.json()["brief"] == "Focus on carryover."


def test_agency_snapshot_requires_cron_secret(monkeypatch):
    monkeypatch.setattr(fin_router, "build_agency_overview", _overview)
    monkeypatch.setattr(fin_router, "store_agency_snapshot", Mock(return_value="ok"))

    denied = client.post("/api/v1/financials/agency/ai-insights/snapshot")
    assert denied.status_code == 401


def test_agency_chat_endpoint_delegates(monkeypatch):
    from app.financial import agency_chat

    monkeypatch.setattr(fin_router, "build_agency_overview", _overview)
    monkeypatch.setattr(fin_router, "_agency_insight_row", lambda: None)
    answer = AsyncMock(
        return_value={
            "reply": "Two items carried over.",
            "guarded": False,
            "truncated": False,
            "capped": False,
            "cost_usd": 0.0,
            "thread_id": "t1",
        }
    )
    monkeypatch.setattr(agency_chat, "answer", answer)

    response = client.post(
        "/api/v1/financials/agency/ai-insights/chat",
        json={"message": "What carried over?"},
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Two items carried over."
    answer.assert_awaited_once()
