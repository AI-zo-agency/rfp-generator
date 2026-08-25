"""HTTP and chat wiring for Teamwork's grounded intelligence surface."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app.financial import router as fin_router
from app.main import app

client = TestClient(app)


def _overview() -> dict:
    return {
        "as_of": "2026-08-25",
        "sync_status": "ok",
        "errors": {},
        "overdue_tasks": [{"id": "task-1", "assignees": []}],
        "upcoming_tasks": [],
        "milestones": [],
        "projects": [],
        "time": {"total_minutes": 0, "billable_minutes": 0},
    }


def test_teamwork_ai_insights_returns_current_cards_when_no_brief_exists(monkeypatch):
    monkeypatch.setattr(fin_router, "overview_from_cache", _overview)
    monkeypatch.setattr(fin_router, "get_latest_insight", lambda *args: None)
    monkeypatch.setattr(fin_router, "list_capacity_snapshots", lambda *args: [])

    body = fin_router.teamwork_ai_insights()

    assert body["status"] == "empty"
    assert body["signals"][0]["id"] == "overdue-unassigned"
    assert body["history"]["ready"] is False


def test_teamwork_regenerate_skips_llm_for_partial_sync(monkeypatch):
    monkeypatch.setattr(
        fin_router,
        "overview_from_cache",
        lambda: {"sync_status": "failed", "errors": {"overview": "down"}},
    )
    monkeypatch.setattr(fin_router, "get_latest_insight", lambda *args: None)
    monkeypatch.setattr(fin_router, "list_capacity_snapshots", lambda *args: [])
    generate = Mock()
    monkeypatch.setattr(fin_router, "generate_teamwork_insight", generate)

    response = client.post("/api/v1/financials/teamwork/ai-insights/regenerate")

    assert response.status_code == 200
    assert not generate.called
    assert response.json()["status"] == "empty"


def test_teamwork_get_ai_insights_has_the_dashboard_shape(monkeypatch):
    monkeypatch.setattr(fin_router, "overview_from_cache", _overview)
    monkeypatch.setattr(fin_router, "get_latest_insight", lambda *args: None)
    monkeypatch.setattr(fin_router, "list_capacity_snapshots", lambda *args: [])

    response = client.get("/api/v1/financials/teamwork/ai-insights")

    assert response.status_code == 200
    body = response.json()
    for key in ("status", "brief", "notes", "signals", "history", "as_of", "generated_at", "provider", "stale"):
        assert key in body


def test_teamwork_regenerate_returns_the_fresh_stored_brief(monkeypatch):
    row = {
        "as_of": "2026-08-25",
        "generated_at": "2026-08-25T08:00:00Z",
        "provider": "gemini",
        "payload": {"brief": "Assign the overdue task.", "notes": {}},
    }
    monkeypatch.setattr(fin_router, "overview_from_cache", _overview)
    monkeypatch.setattr(fin_router, "list_capacity_snapshots", lambda *args: [])
    calls = []
    monkeypatch.setattr(
        fin_router,
        "generate_teamwork_insight",
        lambda site_id, overview, history, as_of: calls.append((site_id, as_of)) or "ok",
    )
    monkeypatch.setattr(fin_router, "get_latest_insight", lambda *args: row)

    response = client.post("/api/v1/financials/teamwork/ai-insights/regenerate")

    assert response.status_code == 200
    assert calls and calls[0][1] == "2026-08-25"
    assert response.json()["generated"] == "ok"
    assert response.json()["brief"] == "Assign the overdue task."


def test_teamwork_chat_returns_unavailable_without_calling_llm_for_forecast_question(monkeypatch):
    from app.financial.teamwork import teamwork_chat

    monkeypatch.setattr(teamwork_chat.financial_llm_cost, "thread_total_usd", lambda _t: 0.0)
    provider = AsyncMock(return_value=("should not be used", "gemini"))
    monkeypatch.setattr(teamwork_chat, "chat_text", provider)

    result = asyncio.run(
        teamwork_chat.answer(
            thread_id="t1",
            question="What will next month's workload be?",
            overview=_overview(),
            history=[],
        )
    )

    assert result["reply"] == teamwork_chat.FORECAST_UNAVAILABLE_REPLY
    assert provider.await_count == 0


def test_teamwork_chat_endpoint_delegates_with_current_evidence(monkeypatch):
    monkeypatch.setattr(fin_router, "overview_from_cache", _overview)
    monkeypatch.setattr(fin_router, "list_capacity_snapshots", lambda *args: [])
    answer = AsyncMock(return_value={"reply": "Assign an owner.", "guarded": False, "truncated": False, "capped": False, "cost_usd": 0.0, "thread_id": "t1"})
    monkeypatch.setattr(fin_router.teamwork_chat, "answer", answer)

    response = client.post(
        "/api/v1/financials/teamwork/ai-insights/chat",
        json={"message": "What needs attention?", "thread_id": "t1"},
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Assign an owner."
    assert answer.await_args.kwargs["history"] == []
