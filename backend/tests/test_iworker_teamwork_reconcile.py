"""Tests for iWorker ↔ Teamwork hours reconciliation."""

from datetime import date

from app.financial.iworker_teamwork_reconcile import (
    STATUS_IWORKER_ONLY,
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_NO_TEAMWORK_MATCH,
    build_reconciliation,
    match_teamwork_person,
    names_match,
    summarize_teamwork_people,
)


def test_names_match_token_containment():
    assert names_match("Murilo", "Murilo Mendes")
    assert names_match("Marcelle Benevides", "Marcelle")
    assert names_match("Kelvin Kiruthu", "Kelvin Kiruthu")
    assert not names_match("Murilo", "Marcelle")


def test_match_teamwork_person_prefers_exact():
    people = [
        {"id": "1", "name": "Murilo", "hours": 2.0},
        {"id": "2", "name": "Murilo Mendes", "hours": 8.0},
    ]
    assert match_teamwork_person("Murilo Mendes", people)["id"] == "2"


def test_summarize_teamwork_people_by_minutes():
    rows = [
        {"user_id": "9", "user_name": "Kelvin Kiruthu", "minutes": 90, "project_name": "Acme"},
        {"user_id": "9", "user_name": "Kelvin Kiruthu", "minutes": 30, "project_name": "Acme"},
        {"user_id": "8", "user_name": "Other", "minutes": 60, "project_name": "Beta"},
    ]
    people = summarize_teamwork_people(rows)
    kelvin = next(p for p in people if p["id"] == "9")
    assert kelvin["hours"] == 2.0
    assert kelvin["projects"][0]["name"] == "Acme"
    assert kelvin["projects"][0]["hours"] == 2.0


def _entry(contractor: str, day: str, hours: float):
    return {
        "contractor": contractor,
        "date": day,
        "hours": hours,
        "amount": hours * 12.5,
        "task": "Work",
        "rate": 12.5,
        "ai_classification": {"is_over_scope": False},
    }


def test_build_reconciliation_match_and_mismatch():
    entries = [
        _entry("Kelvin Kiruthu", "August 25, 2026", 6.5),
        _entry("Murilo Mendes", "August 26, 2026", 10.0),
        _entry("Marcelle Benevides", "August 27, 2026", 4.0),
    ]
    tw = [
        {"id": "1", "name": "Kelvin Kiruthu", "hours": 6.5, "projects": [{"name": "Job A", "hours": 6.5}]},
        {"id": "2", "name": "Murilo Mendes", "hours": 8.0, "projects": [{"name": "Job B", "hours": 8.0}]},
        # Marcelle missing in Teamwork
    ]
    out = build_reconciliation(
        entries,
        start=date(2026, 8, 24),
        end=date(2026, 8, 30),
        roster=["Kelvin Kiruthu", "Murilo Mendes", "Marcelle Benevides"],
        teamwork_people=tw,
    )
    by_name = {r["contractor"]: r for r in out["rows"]}
    assert by_name["Kelvin Kiruthu"]["status"] == STATUS_MATCH
    assert by_name["Murilo Mendes"]["status"] == STATUS_MISMATCH
    assert by_name["Murilo Mendes"]["delta_hours"] == 2.0
    assert by_name["Marcelle Benevides"]["status"] == STATUS_NO_TEAMWORK_MATCH
    assert out["summary"]["mismatched"] == 1
    assert any(s["id"] == "iworker:teamwork_mismatch:Murilo Mendes" for s in out["signals"])
    assert any(s["id"] == "iworker:teamwork_unmatched:Marcelle Benevides" for s in out["signals"])


def test_iworker_only_when_teamwork_zero():
    entries = [_entry("Kelvin Kiruthu", "August 25, 2026", 6.5)]
    tw = [{"id": "1", "name": "Kelvin Kiruthu", "hours": 0.0, "projects": []}]
    out = build_reconciliation(
        entries,
        start=date(2026, 8, 24),
        end=date(2026, 8, 30),
        roster=["Kelvin Kiruthu"],
        teamwork_people=tw,
    )
    assert out["rows"][0]["status"] == STATUS_IWORKER_ONLY
    assert any(s["id"].startswith("iworker:teamwork_missing:") for s in out["signals"])


def test_tolerance_counts_as_match():
    entries = [_entry("Kelvin Kiruthu", "August 25, 2026", 6.5)]
    tw = [{"id": "1", "name": "Kelvin Kiruthu", "hours": 6.75, "projects": []}]
    out = build_reconciliation(
        entries,
        start=date(2026, 8, 24),
        end=date(2026, 8, 30),
        roster=["Kelvin Kiruthu"],
        teamwork_people=tw,
    )
    assert out["rows"][0]["status"] == STATUS_MATCH


def test_merge_zero_hour_known_users_keeps_identity():
    from app.financial.iworker_teamwork_reconcile import merge_zero_hour_known_users

    period = [{"id": "2", "name": "Marcelle Benevides", "hours": 6.6, "projects": []}]
    known = [
        {"id": "1", "name": "Kelvin Kiruthu", "hours": 1.7, "projects": []},
        {"id": "2", "name": "Marcelle Benevides", "hours": 20.0, "projects": []},
    ]
    merged = merge_zero_hour_known_users(
        period,
        known,
        roster=["Kelvin Kiruthu", "Marcelle Benevides", "Murilo Mendes"],
    )
    by_id = {p["id"]: p for p in merged}
    assert by_id["2"]["hours"] == 6.6
    assert by_id["1"]["hours"] == 0.0
    assert by_id["1"]["name"] == "Kelvin Kiruthu"


def test_known_user_with_zero_teamwork_hours_is_iworker_only_not_missing_user():
    """Kelvin logged Teamwork last week; this week only iWorker — still a known user."""
    entries = [_entry("Kelvin Kiruthu", "September 2, 2026", 4.7)]
    tw = [{"id": "1", "name": "Kelvin Kiruthu", "hours": 0.0, "projects": []}]
    out = build_reconciliation(
        entries,
        start=date(2026, 8, 31),
        end=date(2026, 9, 6),
        roster=["Kelvin Kiruthu"],
        teamwork_people=tw,
    )
    row = out["rows"][0]
    assert row["status"] == STATUS_IWORKER_ONLY
    assert row["teamwork_hours"] == 0.0
    assert row["delta_hours"] == 4.7
    assert not any(s["id"].startswith("iworker:teamwork_unmatched:") for s in out["signals"])
    assert any(s["id"] == "iworker:teamwork_missing:Kelvin Kiruthu" for s in out["signals"])


def test_identity_window_includes_logs_after_past_month():
    """July month view must still recognize Marcelle from her August Teamwork logs."""
    from app.financial.iworker_teamwork_reconcile import load_teamwork_hours_for_period

    def fake_list_timelogs(site_id, **filters):
        start = filters.get("time_logged__gte")
        end = filters.get("time_logged__lt")
        # Period query (July only): no Marcelle rows
        if start == "2026-07-01" and end == "2026-08-01":
            return []
        # Identity window through "today" (Sep): includes August log
        return [
            {
                "user_id": "42",
                "user_name": "Marcelle Benevides",
                "minutes": 600,
                "project_name": "zoa 26151",
                "time_logged": "2026-08-25",
            }
        ]

    people = load_teamwork_hours_for_period(
        date(2026, 7, 1),
        date(2026, 7, 31),
        roster=["Marcelle Benevides", "Murilo Mendes"],
        list_timelogs_fn=fake_list_timelogs,
        site_id="zoagency.teamwork.com",
        now=date(2026, 9, 7),
    )
    marcelle = next(p for p in people if p["name"] == "Marcelle Benevides")
    assert marcelle["hours"] == 0.0
    assert marcelle["id"] == "42"
