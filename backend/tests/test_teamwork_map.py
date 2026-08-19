from app.financial.teamwork.teamwork_map import map_project, map_task


def test_map_task_reads_project_id_from_tasklist_meta():
    """Teamwork V3 tasks omit projectId; the project lives on tasklist.meta."""
    task = {
        "id": 48724422,
        "name": "Market and competitive scan",
        "status": "new",
        "dueDate": "2026-08-19",
        "assigneeUserIds": [723314],
        "tasklistId": 3927400,
        "tasklist": {
            "id": 3927400,
            "type": "tasklists",
            "meta": {"name": "01 Strategy and Messaging Platform", "projectId": 1310190},
        },
    }
    included = {
        "projects": {"1310190": {"id": 1310190, "name": "EKL 26140 Eklektik Holiday 2026 go-to-market"}},
        "users": {"723314": {"id": 723314, "firstName": "Sonja", "lastName": "Anderson"}},
    }

    row = map_task(
        site_id="zoagency.teamwork.com",
        task=task,
        included=included,
        task_bucket="overdue",
        synced_at="2026-08-18T00:00:00+00:00",
    )

    assert row["project_id"] == 1310190
    assert row["project_name"] == "EKL 26140 Eklektik Holiday 2026 go-to-market"


def test_map_task_still_uses_top_level_project_id_when_present():
    task = {
        "id": 50,
        "name": "Homepage copy",
        "projectId": 10,
        "project": {"id": 10, "type": "projects"},
        "tasklist": {
            "id": 99,
            "type": "tasklists",
            "meta": {"name": "Wrong list", "projectId": 999},
        },
    }
    included = {"projects": {"10": {"id": 10, "name": "Oakdale"}}}

    row = map_task(
        site_id="zoagency.teamwork.com",
        task=task,
        included=included,
        task_bucket="upcoming",
        synced_at="2026-08-18T00:00:00+00:00",
    )

    assert row["project_id"] == 10
    assert row["project_name"] == "Oakdale"


def test_map_project_uses_sub_status_not_lifecycle_status():
    """Teamwork `status` stays `active` after complete; the UI state is `subStatus`."""
    project = {
        "id": 1289744,
        "name": "EFF 26124 EverFast July Retainer",
        "status": "active",
        "subStatus": "completed",
        "health": 0,
        "startDate": "2026-07-01T00:00:00Z",
        "endDate": "2026-08-17T00:00:00Z",
        "completedAt": "2026-08-17T17:35:33Z",
        "companyName": "Everfast Fiber Networks LLC",
    }

    row = map_project(
        site_id="zoagency.teamwork.com",
        project=project,
        included={},
        synced_at="2026-08-18T00:00:00+00:00",
    )

    assert row["status"] == "completed"
    assert row["due_date"] == "2026-08-17"
    assert row["name"] == "EFF 26124 EverFast July Retainer"


def test_map_project_falls_back_to_status_when_substatus_missing():
    row = map_project(
        site_id="zoagency.teamwork.com",
        project={"id": 10, "name": "Oakdale", "status": "active"},
        included={},
        synced_at="2026-08-18T00:00:00+00:00",
    )
    assert row["status"] == "active"
