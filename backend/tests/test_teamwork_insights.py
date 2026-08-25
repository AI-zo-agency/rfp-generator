from unittest.mock import Mock

import pytest

from app.financial.teamwork import teamwork_insights as insights


def _overview_with_unassigned_overdue() -> dict:
    return {
        "as_of": "2026-08-25",
        "sync_status": "ok",
        "errors": {},
        "overdue_tasks": [
            {"id": "task-1", "name": "Approve homepage", "assignees": []},
        ],
        "upcoming_tasks": [],
        "milestones": [],
        "projects": [],
        "time": {"total_minutes": 0, "billable_minutes": 0},
    }


def _weekly_rows() -> list[dict]:
    return [
        {
            "person_id": "42",
            "person_name": "Alex",
            "as_of": as_of,
            "utilization_pct": utilization,
        }
        for as_of, utilization in (
            ("2026-08-03", 85.0),
            ("2026-08-10", 86.0),
            ("2026-08-17", 87.0),
        )
    ]


def test_build_evidence_includes_current_and_capacity_signals():
    evidence = insights.build_evidence(_overview_with_unassigned_overdue(), _weekly_rows())

    assert any(row["id"] == "overdue-unassigned" for row in evidence["signals"])
    assert any(row["id"] == "capacity:sustained:42" for row in evidence["signals"])


def test_validate_response_keeps_only_notes_for_evidence_signal_ids():
    evidence = {
        "signals": [{"id": "overdue-unassigned", "headline": "One task", "figure": "1"}],
        "history": {},
    }

    out = insights.validate_response(
        {
            "brief": "One task needs an owner.",
            "notes": {
                "overdue-unassigned": "Assign it today.",
                "invented": "Ignore.",
            },
        },
        evidence,
    )

    assert out["notes"] == {"overdue-unassigned": "Assign it today."}


def test_validate_response_rejects_unsupported_quantities():
    evidence = {"signals": [{"id": "overdue-unassigned", "figure": "1"}], "history": {}}

    with pytest.raises(ValueError, match="unsupported quantity"):
        insights.validate_response({"brief": "42 tasks need owners.", "notes": {}}, evidence)


def test_build_messages_says_history_is_still_building_when_not_ready():
    evidence = insights.build_evidence(_overview_with_unassigned_overdue(), [])

    assert "Staffing history is still building" in insights.build_messages(evidence)[1]["content"]


def test_generate_and_store_records_failed_status_without_raising(monkeypatch):
    async def failing_provider(*args, **kwargs):
        return {}, "failed"

    capture_upsert = Mock()
    monkeypatch.setattr(insights, "chat_json_soft", failing_provider)
    monkeypatch.setattr(insights, "upsert_insight", capture_upsert)

    assert insights.generate_and_store("zo", {}, [], "2026-08-25") == "failed"
    assert capture_upsert.call_args.kwargs["status"] == "failed"
    assert capture_upsert.call_args.kwargs["source"] == "teamwork"
