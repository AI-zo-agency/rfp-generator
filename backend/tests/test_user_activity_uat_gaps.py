"""Smoke tests for UAT gap coverage (emit helpers + funnel defs)."""

from __future__ import annotations

from app.services.user_activity import OUTCOMES, emit_activity
from app.services.user_analytics import FUNNELS


def test_outcomes_cover_job_lifecycle():
    assert {"started", "completed", "failed", "cancelled"}.issubset(OUTCOMES)


def test_rfp_funnels_include_personas_and_scan():
    ids = {f["id"] for f in FUNNELS["rfp"]}
    assert "personas_to_build" in ids
    assert "build_to_scan" in ids
    assert "gonogo_to_go" in ids


def test_leads_funnel_ends_at_outreach_ready():
    steps = FUNNELS["leads"][0]["steps"]
    assert "lead.outreach_ready" in steps


def test_emit_pipeline_style_actions_accepted(monkeypatch):
    """Ensure new action verbs pass validation (store=sqlite path)."""
    monkeypatch.setattr(
        "app.services.user_activity._activity_store",
        lambda: "sqlite",
    )
    monkeypatch.setattr(
        "app.services.user_activity.ensure_user_activity_table",
        lambda: None,
    )
    saved: list[dict] = []

    def _fake_sqlite_insert(row: dict) -> None:
        saved.append(row)

    monkeypatch.setattr(
        "app.services.user_activity._insert_sqlite",
        _fake_sqlite_insert,
    )

    for action, outcome, ws in (
        ("proposal.phase_completed", "completed", "rfp"),
        ("proposal.phase_failed", "failed", "rfp"),
        ("proposal.phase_stopped", "cancelled", "rfp"),
        ("rfp.gonogo_completed", "completed", "rfp"),
        ("rfp.pdf_uploaded", "completed", "rfp"),
        ("rfp.deleted", "completed", "rfp"),
        ("rfp.justwin_upserted", "recorded", "rfp"),
        ("kb.doc_deleted", "completed", "rfp"),
        ("financial.client_map_linked", "completed", "financial"),
    ):
        eid = emit_activity(
            workspace=ws,
            action=action,
            summary=f"test {action}",
            outcome=outcome,
            entity_type="test",
            entity_id="t1",
        )
        assert eid, f"emit failed for {action}"
    assert len(saved) == 9
    assert {r["action"] for r in saved} >= {
        "proposal.phase_completed",
        "rfp.gonogo_completed",
        "financial.client_map_linked",
    }
