from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.financial.teamwork import teamwork_sync as sync


def test_auto_backfill_when_not_completed(monkeypatch):
    monkeypatch.setattr(sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(sync, "get_sync_state", lambda site_id: {"backfill_completed_at": None})
    called = {}
    monkeypatch.setattr(sync, "_run_backfill", lambda **k: called.setdefault("backfill", True))
    monkeypatch.setattr(sync, "_run_nightly", lambda **k: called.setdefault("nightly", True))
    monkeypatch.setattr(sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(sync, "finish_sync_run", lambda *a, **k: None)
    monkeypatch.setattr(sync.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    sync.run_sync("auto")
    assert "backfill" in called
    assert "nightly" not in called


def test_auto_nightly_when_backfill_completed(monkeypatch):
    monkeypatch.setattr(sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(
        sync,
        "get_sync_state",
        lambda site_id: {"backfill_completed_at": "2026-08-01T00:00:00+00:00"},
    )
    called = {}
    monkeypatch.setattr(sync, "_run_backfill", lambda **k: called.setdefault("backfill", True))
    monkeypatch.setattr(sync, "_run_nightly", lambda **k: called.setdefault("nightly", True))
    monkeypatch.setattr(sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(sync, "finish_sync_run", lambda *a, **k: None)
    monkeypatch.setattr(sync.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    sync.run_sync("auto")
    assert "nightly" in called
    assert "backfill" not in called


def test_task_snapshot_ignores_cursor_for_time_derived_buckets(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sync.client,
        "list_tasks",
        lambda params: calls.append(params) or ([], {}),
    )
    monkeypatch.setattr(sync, "upsert_tasks", lambda rows: len(rows))

    sync._sync_tasks(site_id="zoagency.teamwork.com", synced_at="2026-08-18T00:00:00+00:00")

    assert calls == [
        {"taskFilter": "overdue"},
        {"taskFilter": "within14"},
    ]


def test_sync_projects_skips_completed_rows(monkeypatch):
    monkeypatch.setattr(
        sync.client,
        "list_projects",
        lambda: (
            [
                {
                    "id": 10,
                    "name": "Oakdale",
                    "status": "active",
                    "subStatus": "current",
                },
                {
                    "id": 1289744,
                    "name": "EFF 26124 EverFast July Retainer",
                    "status": "active",
                    "subStatus": "completed",
                    "completedAt": "2026-08-17T17:35:33Z",
                    "endDate": "2026-08-17T00:00:00Z",
                },
            ],
            {},
        ),
    )
    monkeypatch.setattr(
        sync.client,
        "get_project_summary",
        lambda _project_id: {
            "health": {"0": 1, "1": 0, "2": 0, "3": 0},
            "tasks": {
                "everyone": {
                    "active": 0,
                    "late": 0,
                    "complete": 0,
                    "upcoming": 0,
                    "today": 0,
                    "started": 0,
                    "nodate": 0,
                }
            },
        },
    )
    upserted = []
    monkeypatch.setattr(sync, "upsert_projects", lambda rows: upserted.extend(rows) or len(rows))

    count = sync._sync_projects(site_id="zoagency.teamwork.com", synced_at="2026-08-18T00:00:00+00:00")

    assert count == 1
    assert [row["project_id"] for row in upserted] == [10]
    assert upserted[0]["status"] == "current"


def test_snapshot_prunes_rows_not_returned_by_teamwork(monkeypatch):
    monkeypatch.setattr(sync, "_sync_tasks", lambda **kwargs: {"overdue": 1, "upcoming": 2})
    monkeypatch.setattr(sync, "_sync_projects", lambda **kwargs: 3)
    monkeypatch.setattr(sync, "_sync_people", lambda **kwargs: 4)
    monkeypatch.setattr(sync, "_sync_timelogs", lambda **kwargs: 5)
    monkeypatch.setattr(sync, "_sync_milestones", lambda **kwargs: 6)
    pruned = []
    monkeypatch.setattr(
        sync,
        "prune_snapshot_rows",
        lambda site_id, synced_at: pruned.append((site_id, synced_at)),
    )

    counts = sync._fetch_snapshot(
        site_id="zoagency.teamwork.com",
        started=datetime(2026, 8, 18, tzinfo=timezone.utc),
        synced_at="2026-08-18T00:00:00+00:00",
    )

    assert counts == {
        "projects": 3,
        "people": 4,
        "timelogs": 5,
        "milestones": 6,
        "overdue_tasks": 1,
        "upcoming_tasks": 2,
    }
    assert pruned == [("zoagency.teamwork.com", "2026-08-18T00:00:00+00:00")]


def test_nightly_snapshot_does_not_require_an_incremental_cursor(monkeypatch):
    state_updates = []
    monkeypatch.setattr(sync, "_fetch_snapshot", lambda **kwargs: {})
    monkeypatch.setattr(sync, "_write_panel_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync, "upsert_sync_state", lambda _site, fields: state_updates.append(fields))

    sync._run_nightly(
        site_id="zoagency.teamwork.com",
        started=datetime(2026, 8, 18, tzinfo=timezone.utc),
        run_id="run-1",
    )

    assert state_updates[-1]["last_mode"] == "nightly"


def test_nightly_sync_writes_capacity_snapshot_before_generating_insight(monkeypatch):
    calls = []
    started = datetime(2026, 8, 25, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(
        sync,
        "build_overview",
        lambda *args, **kwargs: {"people": [{"id": "42", "name": "Alex"}]},
    )
    monkeypatch.setattr(sync, "build_daily_capacity_rows", lambda *args: [{"person_id": "42"}])
    monkeypatch.setattr(sync, "upsert_capacity_snapshots", lambda *args: calls.append("snapshot") or 1)
    monkeypatch.setattr(sync, "list_capacity_snapshots", lambda *args: [])
    monkeypatch.setattr(sync, "generate_and_store", lambda *args: calls.append("insight") or "ok")

    sync._write_teamwork_intelligence("zo", started)

    assert calls == ["snapshot", "insight"]


def test_partial_or_failed_overview_does_not_generate_an_insight(monkeypatch):
    generate = Mock()
    monkeypatch.setattr(
        sync,
        "build_overview",
        lambda *args, **kwargs: {"sync_status": "failed", "errors": {"overview": "down"}},
    )
    monkeypatch.setattr(sync, "generate_and_store", generate)

    sync._write_teamwork_intelligence(
        "zo", datetime(2026, 8, 25, 2, tzinfo=timezone.utc)
    )

    generate.assert_not_called()


def test_failed_nightly_does_not_advance_cursor(monkeypatch):
    state = {
        "updated_after_cursor": "2026-08-01T00:00:00+00:00",
        "backfill_completed_at": "x",
    }
    monkeypatch.setattr(sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(sync, "get_sync_state", lambda site_id: state)
    monkeypatch.setattr(sync, "_fetch_snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    advanced = []
    monkeypatch.setattr(sync, "upsert_sync_state", lambda site_id, fields: advanced.append(fields))
    monkeypatch.setattr(sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(sync, "finish_sync_run", lambda *a, **k: None)
    monkeypatch.setattr(sync.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    with pytest.raises(RuntimeError, match="boom"):
        sync.run_sync("nightly")
    assert not any("updated_after_cursor" in fields for fields in advanced if isinstance(fields, dict))


def test_failed_backfill_cache_build_publishes_no_cache_or_cursor(monkeypatch):
    published = []
    monkeypatch.setattr(sync, "get_sync_state", lambda _site: {})
    monkeypatch.setattr(sync, "_fetch_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sync, "_write_panel_cache", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cache failed")))
    monkeypatch.setattr(sync, "upsert_sync_state", lambda site_id, fields: published.append(fields))

    with pytest.raises(RuntimeError, match="cache failed"):
        sync._run_backfill(
            site_id="zoagency.teamwork.com",
            started=datetime(2026, 8, 18, tzinfo=timezone.utc),
            run_id="run-1",
        )

    assert not any("updated_after_cursor" in fields or "backfill_completed_at" in fields for fields in published)


def test_lease_busy_raises_lease_error():
    with pytest.raises(sync.LeaseHeld):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(sync, "try_acquire_lease", lambda *a, **k: False)
            monkeypatch.setattr(sync.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
            sync.run_sync("nightly")
