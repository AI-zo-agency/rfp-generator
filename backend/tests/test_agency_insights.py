from unittest.mock import Mock

import pytest

from app.financial import agency_insights as insights


def _overview() -> dict:
    return {
        "as_of": "2026-09-01",
        "position": {"join_mapped": 2, "join_total": 3, "open_ar": 1000, "live_jobs": 2},
        "jobs": [
            {
                "project_id": "p1",
                "project_name": "Alpha",
                "status": "late",
                "health": "bad",
                "open_ar": 500,
                "join": "mapped",
            },
            {
                "project_id": "p2",
                "project_name": "Beta",
                "status": "current",
                "health": "ok",
                "join": "needs mapping",
            },
        ],
        "unlinked_invoices": [],
        "billed_without_project": [],
    }


def test_build_evidence_includes_weekly_metadata():
    evidence = insights.build_evidence(_overview(), prior_evidence=None)

    assert evidence["cadence"] == "weekly"
    assert evidence["period_label"]
    assert evidence["current_week_label"]
    assert evidence["signals"]
    assert evidence["open_items"]


def test_validate_response_rejects_unsupported_quantities():
    evidence = {
        "signals": [{"id": "carryover:week", "figure": "2"}],
        "open_items": [{"amount": 100}],
    }

    with pytest.raises(ValueError, match="unsupported quantity"):
        insights.validate_response({"brief": "There are 99 open items.", "notes": {}}, evidence)


def test_validate_response_keeps_only_known_signal_notes():
    evidence = {"signals": [{"id": "carryover:week", "figure": "2"}], "open_items": []}

    out = insights.validate_response(
        {
            "brief": "Two items carried over.",
            "notes": {"carryover:week": "Follow up today.", "invented": "Skip."},
        },
        evidence,
    )

    assert out["notes"] == {"carryover:week": "Follow up today."}


def test_generate_and_store_records_failed_status_without_raising(monkeypatch):
    async def failing_provider(*args, **kwargs):
        return {}, "failed"

    capture = Mock()
    monkeypatch.setattr(insights, "chat_json_soft", failing_provider)
    monkeypatch.setattr(insights, "upsert_insight", capture)

    assert insights.generate_and_store("zo", _overview(), None) == "failed"
    assert capture.call_args.kwargs["status"] == "failed"
    assert capture.call_args.kwargs["source"] == "agency"
