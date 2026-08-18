"""Teamwork dashboard overview — mapping, partial failure, cache, auth status."""

from __future__ import annotations

from app.financial.teamwork import overview as ov
from app.financial.teamwork.errors import TeamworkAuthError, TeamworkServerError


def test_map_projects_uses_included_company_and_stats():
    payload_projects = [
        {
            "id": 10,
            "name": "City of Oakdale",
            "status": "active",
            "startDate": "2026-01-01",
            "endDate": "2026-06-30",
            "companyId": 7,
            "company": {"id": 7, "type": "companies"},
            "health": 2,
            "stats": {"tasks": {"active": 8, "completed": 2, "late": 1}},
        }
    ]
    included = {"companies": {"7": {"id": 7, "name": "Oakdale"}}}
    rows = ov.map_projects(payload_projects, included)
    assert len(rows) == 1
    project = rows[0]
    assert project["id"] == "10"
    assert project["name"] == "City of Oakdale"
    assert project["status"] == "active"
    assert project["company_name"] == "Oakdale"
    assert project["health"] == "ok"
    assert project["start_date"] == "2026-01-01"
    assert project["due_date"] == "2026-06-30"
    assert project["tasks_open"] == 8
    assert project["tasks_completed"] == 2
    assert project["tasks_overdue"] == 1
    assert project["progress_pct"] == 20


def test_map_tasks_resolves_assignees_and_project_names():
    tasks = [
        {
            "id": 44,
            "name": "Draft homepage",
            "status": "new",
            "dueDate": "2026-03-01",
            "projectId": 10,
            "priority": "high",
            "assigneeUserIds": [3],
        }
    ]
    included = {
        "users": {"3": {"id": 3, "firstName": "Sonja", "lastName": "Anderson"}},
        "projects": {"10": {"id": 10, "name": "City of Oakdale"}},
    }
    rows = ov.map_tasks(tasks, included)
    assert rows[0]["id"] == "44"
    assert rows[0]["assignees"] == ["Sonja Anderson"]
    assert rows[0]["project_name"] == "City of Oakdale"
    assert rows[0]["priority"] == "high"


def test_map_timelogs_aggregates_by_person_and_project():
    logs = [
        {"id": 1, "minutes": 90, "billable": True, "userId": 3, "projectId": 10, "timeLogged": "2026-08-02T12:00:00Z"},
        {"id": 2, "minutes": 30, "billable": False, "userId": 3, "projectId": 10, "timeLogged": "2026-08-03T12:00:00Z"},
        {"id": 3, "minutes": 60, "billable": True, "userId": 4, "projectId": 11, "timeLogged": "2026-08-04T12:00:00Z"},
    ]
    included = {
        "users": {
            "3": {"id": 3, "firstName": "Sonja", "lastName": "Anderson"},
            "4": {"id": 4, "firstName": "Alex", "lastName": "Kim"},
        },
        "projects": {
            "10": {"id": 10, "name": "Oakdale"},
            "11": {"id": 11, "name": "Retainer"},
        },
    }
    summary = ov.summarize_timelogs(logs, included)
    assert summary["total_minutes"] == 180
    assert summary["billable_minutes"] == 150
    by_person = {row["name"]: row["minutes"] for row in summary["by_person"]}
    assert by_person["Sonja Anderson"] == 120
    assert by_person["Alex Kim"] == 60
    by_project = {row["name"]: row["minutes"] for row in summary["by_project"]}
    assert by_project["Oakdale"] == 120
    assert by_project["Retainer"] == 60


def test_map_people_skips_deleted_and_clients_by_default():
    people = [
        {"id": 1, "firstName": "Sonja", "lastName": "Anderson", "email": "sonja@example.com", "title": "PM", "deleted": False, "isClientUser": False, "type": "account"},
        {"id": 2, "firstName": "Client", "lastName": "User", "email": "c@example.com", "deleted": False, "isClientUser": True, "type": "contact"},
        {"id": 3, "firstName": "Gone", "lastName": "Person", "deleted": True, "type": "account"},
    ]
    rows = ov.map_people(people, {})
    assert [r["id"] for r in rows] == ["1"]
    assert rows[0]["name"] == "Sonja Anderson"
    assert rows[0]["email"] == "sonja@example.com"


def test_map_milestones():
    rows = ov.map_milestones(
        [
            {
                "id": 9,
                "name": "Launch",
                "deadline": "2026-09-01",
                "status": "upcoming",
                "projectId": 10,
                "percentageComplete": 40,
            }
        ],
        {"projects": {"10": {"id": 10, "name": "Oakdale"}}},
    )
    assert rows[0]["project_name"] == "Oakdale"
    assert rows[0]["progress_pct"] == 40


def test_overview_not_configured(monkeypatch):
    monkeypatch.setattr(ov.settings, "teamwork_base_url", "")
    monkeypatch.setattr(ov.settings, "teamwork_api_key", "")
    result = ov.build_overview()
    assert result["connected"] is False
    assert result["projects"] == []
    assert "not configured" in (result["errors"].get("config") or "").lower()


def test_overview_empty_projects(monkeypatch):
    monkeypatch.setattr(ov.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    monkeypatch.setattr(ov.settings, "teamwork_api_key", "k")
    monkeypatch.setattr(ov, "_CACHE", {})
    monkeypatch.setattr(ov, "fetch_projects", lambda: ([], {}))
    monkeypatch.setattr(ov, "fetch_tasks", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_people", lambda: ([], {}))
    monkeypatch.setattr(ov, "fetch_timelogs", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_milestones", lambda: ([], {}))
    result = ov.build_overview()
    assert result["connected"] is True
    assert result["projects"] == []
    assert result["summary"]["project_count"] == 0


def test_overview_partial_failure_keeps_projects(monkeypatch):
    monkeypatch.setattr(ov.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    monkeypatch.setattr(ov.settings, "teamwork_api_key", "k")
    monkeypatch.setattr(ov, "_CACHE", {})
    monkeypatch.setattr(
        ov,
        "fetch_projects",
        lambda: ([{"id": 1, "name": "A", "status": "active"}], {}),
    )
    monkeypatch.setattr(ov, "fetch_tasks", lambda **k: (_ for _ in ()).throw(TeamworkServerError("503")))
    monkeypatch.setattr(ov, "fetch_people", lambda: ([], {}))
    monkeypatch.setattr(ov, "fetch_timelogs", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_milestones", lambda: ([], {}))
    result = ov.build_overview()
    assert result["connected"] is True
    assert len(result["projects"]) == 1
    assert "overdue_tasks" in result["errors"] or "upcoming_tasks" in result["errors"]


def test_overview_auth_failure(monkeypatch):
    monkeypatch.setattr(ov.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    monkeypatch.setattr(ov.settings, "teamwork_api_key", "bad")
    monkeypatch.setattr(ov, "_CACHE", {})
    monkeypatch.setattr(ov, "fetch_projects", lambda: (_ for _ in ()).throw(TeamworkAuthError("401")))
    monkeypatch.setattr(ov, "fetch_tasks", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_people", lambda: ([], {}))
    monkeypatch.setattr(ov, "fetch_timelogs", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_milestones", lambda: ([], {}))
    result = ov.build_overview()
    assert result["connected"] is False
    assert "401" in result["errors"].get("auth", "")


def test_overview_uses_cache(monkeypatch):
    monkeypatch.setattr(ov.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    monkeypatch.setattr(ov.settings, "teamwork_api_key", "k")
    monkeypatch.setattr(ov, "_CACHE", {})
    calls = {"n": 0}

    def fetch_projects():
        calls["n"] += 1
        return ([{"id": 1, "name": "A", "status": "active"}], {})

    monkeypatch.setattr(ov, "fetch_projects", fetch_projects)
    monkeypatch.setattr(ov, "fetch_tasks", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_people", lambda: ([], {}))
    monkeypatch.setattr(ov, "fetch_timelogs", lambda **k: ([], {}))
    monkeypatch.setattr(ov, "fetch_milestones", lambda: ([], {}))
    first = ov.build_overview()
    second = ov.build_overview()
    assert first["projects"][0]["name"] == second["projects"][0]["name"]
    assert calls["n"] == 1


def test_router_status_hides_credentials(monkeypatch):
    from app.core.config import settings
    from app.financial.router import get_teamwork_status

    monkeypatch.setattr(settings, "teamwork_base_url", "")
    monkeypatch.setattr(settings, "teamwork_api_key", "super-secret-key")
    payload = get_teamwork_status()
    dumped = str(payload).lower()
    assert payload["connected"] is False
    assert "super-secret-key" not in dumped
    assert "api_key" not in dumped
