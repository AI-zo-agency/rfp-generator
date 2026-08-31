"""Snapshot → generate chain for Agency weekly intelligence."""

from __future__ import annotations

from unittest.mock import Mock

from app.financial import agency_insights as insights


def _overview() -> dict:
    return {
        "as_of": "2026-09-05",
        "position": {"join_mapped": 1, "join_total": 2, "open_ar": 500, "live_jobs": 1},
        "jobs": [
            {
                "project_id": "p1",
                "project_name": "Alpha",
                "status": "late",
                "health": "bad",
                "open_ar": 500,
                "join": "mapped",
            },
        ],
        "unlinked_invoices": [],
        "billed_without_project": [],
    }


def test_snapshot_then_generate_preserves_carryover_chain(monkeypatch):
    stored: dict[str, dict] = {}

    def fake_upsert(**kwargs):
        key = kwargs["as_of"]
        stored[key] = kwargs
        return {"id": key}

    async def fake_llm(*args, **kwargs):
        return {"brief": "One delivery item carried over.", "notes": {}}, "gemini"

    monkeypatch.setattr(insights, "upsert_insight", fake_upsert)
    monkeypatch.setattr(insights, "chat_json_soft", fake_llm)
    monkeypatch.setattr(insights, "today_pt", lambda: __import__("datetime").date(2026, 9, 5))

    overview = _overview()
    assert insights.store_snapshot("zo", overview, None) == "ok"
    friday_evidence = stored["2026-08-31"]["evidence"]
    assert any(row["id"] == "delivery:p1" for row in friday_evidence["open_items"])

    monkeypatch.setattr(insights, "today_pt", lambda: __import__("datetime").date(2026, 9, 7))
    assert insights.generate_and_store("zo", overview, friday_evidence) == "ok"
    monday_row = stored["2026-08-31"]
    assert monday_row["payload"]["brief"] == "One delivery item carried over."
    carryover_ids = {row["id"] for row in monday_row["evidence"]["carryover"]}
    assert "delivery:p1" in carryover_ids
