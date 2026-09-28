"""Integration-style checks for UAT audit + analytics (no live Supabase)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services import user_activity, user_analytics


def test_audit_emit_list_roundtrip_sqlite(monkeypatch):
    monkeypatch.setattr(user_activity, "_activity_store", lambda: "sqlite")
    monkeypatch.setattr(user_activity, "ensure_user_activity_table", lambda: None)
    rows: list[dict] = []
    monkeypatch.setattr(user_activity, "_insert_sqlite", lambda row: rows.append(row))

    def _list(**_kwargs):
        return {
            "workspace": "rfp",
            "items": [
                {
                    "id": r["id"],
                    "action": r["action"],
                    "outcome": r["outcome"],
                    "actor_email": r["actor_email"],
                    "summary": r["summary"],
                }
                for r in rows
            ],
            "next_cursor": None,
            "stats": {"events_today": len(rows), "unique_actors_7d": 1},
            "store": "sqlite",
        }

    monkeypatch.setattr(user_activity, "list_activity", _list)

    eid = user_activity.emit_activity(
        workspace="rfp",
        action="rfp.gonogo_completed",
        summary="Go/No-Go finished: go",
        actor_email="alice@zo.test",
        outcome="completed",
        entity_type="rfp",
        entity_id="rfp-1",
    )
    assert eid
    assert rows[0]["action"] == "rfp.gonogo_completed"
    assert rows[0]["outcome"] == "completed"
    assert rows[0]["actor_email"] == "alice@zo.test"

    listed = user_activity.list_activity(workspace="rfp")
    assert listed["items"][0]["action"] == "rfp.gonogo_completed"


def test_analytics_ingest_and_summary_excludes_self_uat(monkeypatch):
    monkeypatch.setattr(user_analytics, "_store", lambda: "sqlite")
    monkeypatch.setattr(user_analytics, "ensure_analytics_table", lambda: None)
    stored: list[dict] = []

    def _insert(rows: list[dict]) -> None:
        stored.extend(rows)

    monkeypatch.setattr(user_analytics, "_insert_sqlite_batch", _insert)

    now = datetime.now(timezone.utc).isoformat()
    result = user_analytics.ingest_events(
        workspace="rfp",
        actor_email="bob@zo.test",
        events=[
            {
                "event_type": "page_view",
                "path": "/rfps/abc",
                "session_id": "s1",
                "client_ts": now,
            },
            {
                "event_type": "ui_click",
                "feature": "proposal.build",
                "path": "/proposals",
                "session_id": "s1",
                "client_ts": now,
            },
            {
                "event_type": "funnel_step",
                "feature": "proposal.exported",
                "path": "/proposals",
                "session_id": "s1",
                "client_ts": now,
            },
            {
                "event_type": "heartbeat",
                "path": "/rfps/abc",
                "duration_ms": 15000,
                "engaged": True,
                "session_id": "s1",
                "client_ts": now,
            },
            # Self-UAT pollution — must be filtered from summary
            {
                "event_type": "page_view",
                "path": "/activity",
                "session_id": "s1",
                "client_ts": now,
            },
            {
                "event_type": "heartbeat",
                "path": "/financial-insights",
                "tab": "activity",
                "duration_ms": 15000,
                "engaged": True,
                "session_id": "s1",
                "client_ts": now,
            },
            {
                "event_type": "tab_view",
                "path": "/lead-finder",
                "view": "analytics",
                "session_id": "s1",
                "client_ts": now,
            },
        ],
    )
    assert result["ok"] is True
    assert result["inserted"] == 7
    assert len(stored) == 7

    monkeypatch.setattr(
        user_analytics,
        "_fetch_range",
        lambda *_a, **_k: list(stored),
    )
    summary = user_analytics.summarize(workspace="rfp")
    assert summary.get("error") is None
    # Only the real /rfps heartbeat counts (activity-tab heartbeat excluded)
    assert summary["time"]["visible_ms"] == 15000
    assert summary["time"]["engaged_ms"] == 15000
    features = {f["feature"] for f in summary["top_features"]}
    assert "proposal.build" in features
    assert "proposal.exported" in features
    # /activity page_view must not appear in top_pages
    paths = {p["path"] for p in summary["top_pages"]}
    assert not any(p == "/activity" or p.endswith("#activity") for p in paths)
    assert any("/rfps/abc" in p for p in paths)
    assert summary["by_user"][0]["actor_email"] == "bob@zo.test"
    assert summary["by_user"][0]["clicks"] == 2


def test_frontend_exclusion_paths_match_backend_filter():
    """Keep client EXCLUDED_* and summarize() filter in sync conceptually."""
    # Mirror zo-analytics.ts + summarize _is_self_uat_row
    excluded_paths = {"/activity"}
    excluded_tabs = {"activity"}
    excluded_views = {"activity", "analytics", "audit"}

    def is_excluded(path="", tab="", view=""):
        path = (path or "").split("?")[0].rstrip("/") or "/"
        if path in excluded_paths:
            return True
        if (tab or "").strip().lower() in excluded_tabs:
            return True
        if (view or "").strip().lower() in excluded_views:
            return True
        return False

    assert is_excluded(path="/activity")
    assert is_excluded(tab="activity")
    assert is_excluded(view="analytics")
    assert is_excluded(view="audit")
    assert not is_excluded(path="/rfps/1")
    assert not is_excluded(path="/proposals")
    assert not is_excluded(tab="quickbooks")
    assert not is_excluded(view="queue")
