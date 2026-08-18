from app.financial.teamwork import teamwork_panels_from_db as panels


def test_build_overview_from_mirror_rows(monkeypatch):
    projects = [
        {
            "project_id": 10,
            "name": "Oakdale",
            "status": "active",
            "health": "ok",
            "company_name": "City of Oakdale",
            "start_date": "2026-01-01",
            "due_date": "2026-06-30",
            "tasks_open": 8,
            "tasks_completed": 2,
            "tasks_overdue": 1,
            "progress_pct": 20,
        }
    ]
    overdue = [
        {
            "task_id": 50,
            "name": "Homepage copy",
            "project_name": "Oakdale",
            "assignee_names": ["Sonja Anderson"],
            "due_date": "2026-08-15",
            "task_bucket": "overdue",
        }
    ]
    upcoming = [
        {
            "task_id": 51,
            "name": "Launch QA",
            "project_name": "Oakdale",
            "assignee_names": ["Alex Kim"],
            "due_date": "2026-08-22",
            "task_bucket": "upcoming",
        }
    ]
    people = [{"person_id": 7, "name": "Sonja Anderson", "email": "sonja@example.com", "title": "PM", "company_name": "zö"}]
    milestones = [{"milestone_id": 3, "name": "Launch", "status": "late", "project_name": "Oakdale", "due_date": "2026-08-10", "progress_pct": 40}]
    timelogs = [
        {"timelog_id": 1, "minutes": 90, "billable": True, "user_id": 7, "user_name": "Sonja Anderson", "project_id": 10, "project_name": "Oakdale"},
        {"timelog_id": 2, "minutes": 30, "billable": False, "user_id": 8, "user_name": "Alex Kim", "project_id": 10, "project_name": "Oakdale"},
    ]

    monkeypatch.setattr(panels, "list_projects", lambda site_id, **filters: projects)
    monkeypatch.setattr(
        panels,
        "list_tasks",
        lambda site_id, **filters: overdue if filters.get("task_bucket") == "overdue" else upcoming,
    )
    monkeypatch.setattr(panels, "list_people", lambda site_id, **filters: people)
    monkeypatch.setattr(panels, "list_milestones", lambda site_id, **filters: milestones)
    monkeypatch.setattr(panels, "list_timelogs", lambda site_id, **filters: timelogs)

    payload = panels.build_overview("zoagency.teamwork.com", as_of="2026-08-18")
    assert payload["summary"]["project_count"] == 1
    assert payload["summary"]["overdue_task_count"] == 1
    assert payload["summary"]["upcoming_task_count"] == 1
    assert payload["summary"]["late_milestone_count"] == 1
    assert payload["summary"]["hours_this_month"] == 2.0
    assert payload["projects"][0]["name"] == "Oakdale"
    assert payload["overdue_tasks"][0]["assignees"] == ["Sonja Anderson"]
    assert payload["time"]["billable_minutes"] == 90


def test_build_overview_empty_rows(monkeypatch):
    monkeypatch.setattr(panels, "list_projects", lambda site_id, **filters: [])
    monkeypatch.setattr(panels, "list_tasks", lambda site_id, **filters: [])
    monkeypatch.setattr(panels, "list_people", lambda site_id, **filters: [])
    monkeypatch.setattr(panels, "list_milestones", lambda site_id, **filters: [])
    monkeypatch.setattr(panels, "list_timelogs", lambda site_id, **filters: [])

    payload = panels.build_overview("zoagency.teamwork.com", as_of="2026-08-18")
    assert payload["summary"]["project_count"] == 0
    assert payload["projects"] == []
    assert payload["time"]["by_person"] == []

