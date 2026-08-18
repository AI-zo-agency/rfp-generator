from datetime import datetime, timezone

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


def test_failed_nightly_does_not_advance_cursor(monkeypatch):
    state = {
        "updated_after_cursor": "2026-08-01T00:00:00+00:00",
        "backfill_completed_at": "x",
    }
    monkeypatch.setattr(sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(sync, "get_sync_state", lambda site_id: state)
    monkeypatch.setattr(sync, "_fetch_incremental", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
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
    monkeypatch.setattr(sync, "ENTITY_FETCHERS", {})
    monkeypatch.setattr(sync, "get_sync_state", lambda _site: {})
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

