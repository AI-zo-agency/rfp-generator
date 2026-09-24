"""Smoke tests for user activity emit/list helpers (no Supabase required)."""

from __future__ import annotations

from app.services.user_activity import WORKSPACES, emit_activity, list_activity


def test_workspaces_and_outcomes_are_locked():
    assert WORKSPACES == frozenset({"rfp", "financial", "leads"})
    from app.services.user_activity import OUTCOMES

    assert "started" in OUTCOMES
    assert "completed" in OUTCOMES


def test_emit_rejects_invalid_workspace(monkeypatch):
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not touch supabase")

    monkeypatch.setattr(
        "app.services.supabase_db.use_supabase_db",
        lambda: True,
    )
    # If workspace invalid, emit returns None before DB.
    assert emit_activity(
        workspace="nope",
        action="x",
        summary="y",
    ) is None
    assert called["n"] == 0


def test_list_invalid_workspace_returns_empty():
    result = list_activity(workspace="nope")
    assert result["items"] == []
    assert result["next_cursor"] is None
