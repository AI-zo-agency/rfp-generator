import asyncio

from fastapi.testclient import TestClient

from app.financial.router import (
    generate_ai_financial_insights,
    get_audit_queue,
    get_checklist,
    get_iworker_timesheets,
    get_sources_status,
)
from app.main import app

def test_iworker_timesheets_defaults_are_plain_none():
    """Direct Python callers must not receive Query objects."""
    assert get_iworker_timesheets.__defaults__[:2] == (None, None)
    assert "Query" not in type(get_iworker_timesheets.__defaults__[0]).__name__


def test_audit_queue_skips_iworker_when_cache_empty(monkeypatch):
    """audit-queue must not trigger sheet + classifier when timesheets were never loaded."""
    from app.financial import router

    monkeypatch.setattr(router, "_TIMESHEET_CACHE", {})

    def boom(*_args, **_kwargs):
        raise AssertionError("audit-queue must not call get_iworker_timesheets")

    monkeypatch.setattr(router, "get_iworker_timesheets", boom)
    assert get_audit_queue() == {"audit_items": []}


def test_audit_queue_filters_to_selected_week(monkeypatch):
    from app.financial import router

    payload = {
        "timesheets": [
            {
                "date": "May 13, 2026",
                "hours": 9.0,
                "amount": 90.0,
                "task": "Huge May task",
                "ai_classification": {"is_over_scope": False, "topic": "Huge May task", "work_category": "Unknown"},
            },
            {
                "date": "Nov 12, 2025",
                "hours": 9.0,
                "amount": 90.0,
                "task": "Huge Nov task",
                "ai_classification": {"is_over_scope": True, "topic": "Huge Nov task", "detected_round": 3, "ai_reasoning": "R3"},
            },
        ]
    }
    monkeypatch.setattr(router, "_TIMESHEET_CACHE", {"default": (0.0, payload)})
    items = get_audit_queue(granularity="week", period_start="2026-05-11")["audit_items"]
    reasons = " ".join(i["reason"] for i in items)
    assert "Huge May task" in reasons
    assert "Huge Nov task" not in reasons


def test_audit_queue_includes_capacity_signals(monkeypatch):
    from datetime import date

    from app.financial import iworker_period_insights as period
    from app.financial import router

    payload = {
        "timesheets": [
            {
                "date": "May 13, 2026",
                "hours": 1.0,
                "amount": 12.5,
                "rate": 12.5,
                "contractor": "Murilo",
                "task": "Light week",
                "ai_classification": {
                    "is_over_scope": False,
                    "topic": "Light week",
                    "work_category": "Video",
                },
            }
        ]
    }
    monkeypatch.setattr(router, "_TIMESHEET_CACHE", {"default": (0.0, payload)})
    monkeypatch.setattr(period, "today_in_tz", lambda now=None, tz_name=None: date(2026, 5, 13))
    items = get_audit_queue(granularity="week", period_start="2026-05-11")["audit_items"]
    assert any(i["id"] == "iworker:underlogged:Murilo" for i in items)


def test_iworker_timesheets_data(monkeypatch):
    from app.financial import router

    monkeypatch.setattr(router, "upsert_period_snapshots", lambda rows: 0)
    monkeypatch.setattr(router, "list_period_history", lambda *_a, **_k: [])

    res = get_iworker_timesheets(period_start="2026-05-11", granularity="week")
    assert res["contractor"] == "All Contractors"
    assert "Connected" in res["status"]
    assert len(res["timesheets"]) > 0
    assert "weekly_totals" not in res
    assert "unbilled_risk_amount" not in res.get("summary", {})
    insights = res["period_insights"]
    assert insights["granularity"] == "week"
    assert insights["selected"]["start"] == "2026-05-11"
    assert insights["current"]["hours"] > 0
    assert "hours_pct" in insights["delta"]
    assert isinstance(insights["contractors"], list)
    assert "period_history" in res
    assert res["meta"]["unparsed_date_count"] >= 0

def test_iworker_sync_requires_cron_secret():
    client = TestClient(app)
    response = client.post("/api/v1/financials/iworker/sync")
    assert response.status_code == 401


def test_checklist_data():
    res = get_checklist()
    assert res["total_features"] == 19
    assert len(res["checklist"]) == 19
    assert len(res["phases"]) == 5

def test_sources_status(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "quickbooks_client_id", "id")
    monkeypatch.setattr(settings, "quickbooks_client_secret", "secret")
    monkeypatch.setattr(settings, "quickbooks_refresh_token", "rt")
    monkeypatch.setattr(settings, "quickbooks_realm_id", "realm")

    res = get_sources_status()
    sources = res["sources"]
    assert len(sources) == 5
    iworker = next(s for s in sources if s["name"] == "iWorker Timesheets")
    assert iworker["active_data"] is True
    qb = next(s for s in sources if s["name"] == "QuickBooks API")
    assert qb["active_data"] is True
    assert qb["status"] == "Connected"

def test_ai_insights(monkeypatch):
    from app.financial import router

    async def fake_chat_json(messages, **_k):
        return (
            {
                "leadership_brief_text": "Brief",
                "top_3_risks": ["r1", "r2", "r3"],
                "top_3_wins": ["w1", "w2", "w3"],
                "margin_recommendations": ["m1", "m2", "m3"],
            },
            "test",
        )

    monkeypatch.setattr(router, "chat_json", fake_chat_json)
    res = asyncio.run(generate_ai_financial_insights())
    assert res["status"] == "success"
    assert len(res["summary"]["top_3_risks"]) == 3
    assert len(res["summary"]["top_3_wins"]) == 3


def test_ai_insights_prompt_uses_period_metrics(monkeypatch):
    from app.financial import router

    captured = {}

    async def fake_chat_json(messages, **_k):
        captured["user"] = messages[1]["content"]
        return (
            {
                "leadership_brief_text": "Brief",
                "top_3_risks": ["r1", "r2", "r3"],
                "top_3_wins": ["w1", "w2", "w3"],
                "margin_recommendations": ["m1", "m2", "m3"],
            },
            "test",
        )

    monkeypatch.setattr(router, "chat_json", fake_chat_json)
    res = asyncio.run(
        generate_ai_financial_insights(granularity="week", period_start="2026-05-11")
    )
    assert res["stats"]["total_hours"] > 0
    assert "2026-05-11" in captured["user"] or "May 11" in captured["user"]
    assert "lifetime" not in captured["user"].lower()


def test_teamwork_overview_reads_cached_payload(monkeypatch):
    from app.financial import router

    monkeypatch.setattr(
        router,
        "get_teamwork_panel_cache",
        lambda site_id: {
            "payload": {"summary": {"project_count": 2}, "projects": [], "errors": {}},
            "as_of": "2026-08-18",
            "computed_at": "2026-08-18T00:00:00+00:00",
        },
        raising=False,
    )
    monkeypatch.setattr(
        router,
        "get_teamwork_sync_state",
        lambda site_id: {"last_success_at": "2026-08-18T00:00:00+00:00"},
        raising=False,
    )
    monkeypatch.setattr(
        router,
        "build_teamwork_overview",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not live read")),
        raising=False,
    )

    client = TestClient(app)
    response = client.get("/api/v1/financials/teamwork/overview")
    assert response.status_code == 200
    assert response.json()["summary"]["project_count"] == 2
    assert response.json()["synced_at"] == "2026-08-18T00:00:00+00:00"


def test_teamwork_overview_includes_base_url_when_configured(monkeypatch):
    from app.financial import router

    monkeypatch.setattr(
        router,
        "get_teamwork_panel_cache",
        lambda site_id: {
            "payload": {"summary": {"project_count": 1}, "projects": [], "errors": {}},
            "as_of": "2026-08-18",
            "computed_at": "2026-08-18T00:00:00+00:00",
        },
        raising=False,
    )
    monkeypatch.setattr(router, "get_teamwork_sync_state", lambda site_id: {}, raising=False)
    monkeypatch.setattr(router.settings, "teamwork_base_url", "https://zoagency.teamwork.com", raising=False)
    monkeypatch.setattr(router.settings, "teamwork_api_key", "test_key", raising=False)
    monkeypatch.setattr(router, "teamwork_origin", lambda: "https://zoagency.teamwork.com", raising=False)

    client = TestClient(app)
    response = client.get("/api/v1/financials/teamwork/overview")

    assert response.status_code == 200
    assert response.json()["base_url"] == "https://zoagency.teamwork.com"


def test_teamwork_overview_base_url_is_none_when_not_configured(monkeypatch):
    from app.financial import router

    monkeypatch.setattr(router, "get_teamwork_panel_cache", lambda site_id: None, raising=False)
    monkeypatch.setattr(router, "get_teamwork_sync_state", lambda site_id: {}, raising=False)
    monkeypatch.setattr(router.settings, "teamwork_base_url", "", raising=False)
    monkeypatch.setattr(router.settings, "teamwork_api_key", "", raising=False)

    client = TestClient(app)
    response = client.get("/api/v1/financials/teamwork/overview")

    assert response.status_code == 200
    assert response.json()["base_url"] is None
