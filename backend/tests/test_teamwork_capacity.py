from app.financial.teamwork import teamwork_capacity as capacity


def overview_with_person(person_id: str, name: str, *, minutes: int) -> dict:
    return {
        "people": [{"id": person_id, "name": name}],
        "time": {
            "by_person": [
                {
                    "id": person_id,
                    "name": name,
                    "minutes": minutes,
                    "billable_minutes": 0,
                    "breakdown": [],
                }
            ]
        },
        "overdue_tasks": [],
        "upcoming_tasks": [],
        "projects": [],
    }


def weekly_rows(person_id: str, name: str, utilizations: list[float]) -> list[dict]:
    return [
        {
            "person_id": person_id,
            "person_name": name,
            "as_of": f"2026-08-{3 + index * 7:02d}",
            "week_start": f"2026-08-{3 + index * 7:02d}",
            "utilization_pct": utilization,
        }
        for index, utilization in enumerate(utilizations)
    ]


def test_build_daily_capacity_rows_sets_85_percent_at_2040_minutes():
    rows = capacity.build_daily_capacity_rows(
        overview_with_person("42", "Alex", minutes=2_040), "2026-08-25"
    )

    assert rows == [
        {
            "person_id": "42",
            "person_name": "Alex",
            "logged_minutes": 2_040,
            "billable_minutes": 0,
            "capacity_minutes": 2_400,
            "utilization_pct": 85.0,
            "overdue_tasks": 0,
            "due_soon_tasks": 0,
            "active_projects": 0,
            "budget_exposed_projects": 0,
        }
    ]


def test_capacity_signals_marks_three_consecutive_weeks_as_high_impact():
    signals = capacity.capacity_signals(weekly_rows("42", "Alex", [85.0, 90.0, 88.0]))

    assert signals[0]["id"] == "capacity:sustained:42"
    assert signals[0]["severity"] == "critical"
    assert "3 consecutive weeks" in signals[0]["detail"]


def test_capacity_signals_with_less_than_three_weeks_reports_history_building():
    signals = capacity.capacity_signals(weekly_rows("42", "Alex", [90.0, 90.0]))

    assert signals == []
    assert capacity.capacity_history_state(weekly_rows("42", "Alex", [90.0, 90.0])) == {
        "weeks_available": 2,
        "ready": False,
    }


def test_capacity_signals_emits_hiring_signal_for_two_sustained_people():
    history = weekly_rows("42", "Alex", [85.0, 86.0, 87.0]) + weekly_rows(
        "43", "Sam", [90.0, 86.0, 85.0]
    )

    signals = capacity.capacity_signals(history)

    assert any(signal["id"] == "capacity:hiring" for signal in signals)


def test_capacity_signals_uses_latest_daily_snapshot_in_each_calendar_week():
    history = [
        {
            "person_id": "42",
            "person_name": "Alex",
            "as_of": "2026-08-17",
            "utilization_pct": 90.0,
        },
        {
            "person_id": "42",
            "person_name": "Alex",
            "as_of": "2026-08-23",
            "utilization_pct": 10.0,
        },
        *weekly_rows("42", "Alex", [85.0, 86.0]),
    ]

    assert capacity.capacity_signals(history) == []


def test_is_sustained_rejects_high_utilization_when_a_week_is_missing():
    rows = [
        {"week_start": "2026-08-03", "utilization_pct": 90.0},
        {"week_start": "2026-08-10", "utilization_pct": 90.0},
        {"week_start": "2026-08-24", "utilization_pct": 90.0},
    ]

    assert capacity.is_sustained(rows) is False


def test_capacity_signals_does_not_emit_team_or_hiring_for_gapped_weeks():
    def gapped_rows(person_id: str, name: str) -> list[dict]:
        return [
            {
                "person_id": person_id,
                "person_name": name,
                "as_of": as_of,
                "utilization_pct": 90.0,
            }
            for as_of in ("2026-08-03", "2026-08-10", "2026-08-24")
        ]

    signals = capacity.capacity_signals(
        gapped_rows("42", "Alex") + gapped_rows("43", "Sam")
    )

    assert not any(signal["id"].startswith("capacity:sustained:") for signal in signals)
    assert not any(signal["id"] in {"capacity:team", "capacity:hiring"} for signal in signals)
