from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.financial import iworker_snapshots as repo

PT = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 5, 13, 15, 0, tzinfo=PT)


class FakeQuery:
    def __init__(self, data=None):
        self.data = data or []
        self.calls = []

    def select(self, *_a, **_k):
        self.calls.append(("select",))
        return self

    def eq(self, col, val):
        self.calls.append(("eq", col, val))
        return self

    def order(self, *args, **_k):
        self.calls.append(("order", *args))
        return self

    def limit(self, n):
        self.calls.append(("limit", n))
        return self

    def upsert(self, row, on_conflict=None):
        self.calls.append(("upsert", row, on_conflict))
        return self

    def execute(self):
        return MagicMock(data=self.data)


class FakeClient:
    def __init__(self, query: FakeQuery):
        self._query = query

    def table(self, name):
        assert name == "iworker_period_snapshots"
        self._query.calls.append(("table", name))
        return self._query


def _entry(**kwargs):
    row = {
        "date": "May 13, 2026",
        "hours": 4.0,
        "amount": 50.0,
        "contractor": "Murilo",
        "task": "Edits",
        "rate": 12.5,
        "ai_classification": {"is_over_scope": False},
    }
    row.update(kwargs)
    return row


def test_upsert_period_snapshots_uses_composite_conflict(monkeypatch):
    q = FakeQuery(data=[])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    rows = [
        {
            "spreadsheet_id": "sheet-1",
            "granularity": "week",
            "period_start": "2026-05-11",
            "period_end": "2026-05-17",
            "contractor": "*",
            "hours": 10.0,
            "spend_usd": 100.0,
            "scope_risk_usd": 0.0,
            "entries_count": 2,
            "active_contractors": 1,
            "payload": {},
            "captured_at": "2026-05-13T22:00:00+00:00",
        }
    ]
    assert repo.upsert_period_snapshots(rows) == 1
    assert ("table", "iworker_period_snapshots") in q.calls
    assert any(
        c[0] == "upsert" and c[2] == "spreadsheet_id,granularity,period_start,contractor"
        for c in q.calls
    )


def test_list_period_history_filters_grain(monkeypatch):
    q = FakeQuery(data=[{"period_start": "2026-05-04", "hours": 8.0, "granularity": "week"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    rows = repo.list_period_history("sheet-1", "week")
    assert rows[0]["hours"] == 8.0
    assert ("eq", "spreadsheet_id", "sheet-1") in q.calls
    assert ("eq", "granularity", "week") in q.calls
    assert ("eq", "contractor", "*") in q.calls


def test_rows_for_current_periods_includes_aggregate_and_contractors():
    entries = [
        _entry(date="May 13, 2026", hours=10.0, amount=100.0),
        _entry(date="May 6, 2026", hours=8.0, amount=80.0, contractor="Other"),
    ]
    captured_at = "2026-05-13T22:00:00+00:00"
    rows = repo.rows_for_current_periods(
        "sheet-1",
        entries,
        now=NOW,
        captured_at=captured_at,
    )
    assert len(rows) == 6  # week/month × (aggregate + 2 contractors)
    aggregate = [r for r in rows if r["contractor"] == "*"]
    assert len(aggregate) == 2
    assert all(r["spreadsheet_id"] == "sheet-1" for r in rows)
    assert all(r["captured_at"] == captured_at for r in rows)
    week_agg = next(r for r in aggregate if r["granularity"] == "week")
    assert week_agg["period_start"] == "2026-05-11"
    assert week_agg["period_end"] == "2026-05-17"
