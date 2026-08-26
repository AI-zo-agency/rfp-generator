from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.financial.teamwork import teamwork_repository as repo


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.operation = None
        self.rows = None
        self.filters = []
        self._range = None

    def select(self, *_args):
        self.operation = "select"
        return self

    def upsert(self, rows, on_conflict=None):
        self.operation = "upsert"
        self.rows = rows if isinstance(rows, list) else [rows]
        self.client.calls.append(("upsert", self.name, self.rows, on_conflict))
        return self

    def insert(self, rows):
        self.operation = "insert"
        self.rows = rows if isinstance(rows, list) else [rows]
        self.client.calls.append(("insert", self.name, self.rows, None))
        return self

    def update(self, fields):
        self.operation = "update"
        self.rows = fields
        self.client.calls.append(("update", self.name, fields, None))
        return self

    def delete(self):
        self.operation = "delete"
        self.client.calls.append(("delete", self.name, None, None))
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def gt(self, column, value):
        self.filters.append(("gt", column, value))
        return self

    def gte(self, column, value):
        self.filters.append(("gte", column, value))
        return self

    def lt(self, column, value):
        self.filters.append(("lt", column, value))
        return self

    def lte(self, column, value):
        self.filters.append(("lte", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, values))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def limit(self, *_args):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def _matches(self, row):
        for op, column, value in self.filters:
            actual = row.get(column)
            if op == "eq" and actual != value:
                return False
            if op == "neq" and actual == value:
                return False
            if op == "in" and actual not in value:
                return False
            if op == "is" and actual is not value:
                return False
            if op == "gt" and not actual > value:
                return False
            if op == "gte" and not actual >= value:
                return False
            if op == "lt" and not actual < value:
                return False
            if op == "lte" and not actual <= value:
                return False
        return True

    def execute(self):
        stored = self.client.store.setdefault(self.name, [])
        matching = [row for row in stored if self._matches(row)]
        if self.operation in {"upsert", "insert"}:
            stored.extend(self.rows)
            return FakeResponse(self.rows)
        if self.operation == "update":
            for row in matching:
                row.update(self.rows)
            return FakeResponse(matching)
        if self.operation == "delete":
            self.client.store[self.name] = [
                row for row in stored if not self._matches(row)
            ]
            return FakeResponse(matching)
        if self._range is not None:
            start, end = self._range
            matching = matching[start : end + 1]
        return FakeResponse(matching)


class FakeClient:
    def __init__(self):
        self.store = {}
        self.calls = []

    def table(self, name):
        return FakeTable(self, name)


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(repo, "_get_client", lambda: fake)
    return fake


def test_upsert_projects_uses_expected_conflict_key(client):
    rows = [
        {
            "site_id": "zoagency.teamwork.com",
            "project_id": 1,
            "name": "Oakdale",
            "synced_at": "2026-08-18T00:00:00+00:00",
        }
    ]

    assert repo.upsert_projects(rows) == 1

    op, table, payload, conflict = client.calls[-1]
    assert op == "upsert"
    assert table == "teamwork_projects"
    assert conflict == "site_id,project_id"
    assert payload[0]["project_id"] == 1


def test_upsert_tasks_and_people_batches_and_round_trip(client):
    tasks = [
        {
            "site_id": "zoagency.teamwork.com",
            "task_id": 1,
            "project_id": 10,
            "task_bucket": "overdue",
            "synced_at": "now",
        }
    ]
    people = [
        {
            "site_id": "zoagency.teamwork.com",
            "person_id": 7,
            "name": "Sonja Anderson",
            "synced_at": "now",
        }
    ]

    assert repo.upsert_tasks(tasks) == 1
    assert repo.upsert_people(people) == 1
    assert repo.list_tasks("zoagency.teamwork.com", task_bucket="overdue")[0]["task_id"] == 1
    assert repo.list_people("zoagency.teamwork.com")[0]["person_id"] == 7


def test_upsert_timelogs_and_milestones_round_trip(client):
    timelogs = [
        {
            "site_id": "zoagency.teamwork.com",
            "timelog_id": 9,
            "project_id": 10,
            "minutes": 120,
            "time_logged": "2026-08-18T00:00:00Z",
            "synced_at": "now",
        }
    ]
    milestones = [
        {
            "site_id": "zoagency.teamwork.com",
            "milestone_id": 3,
            "project_id": 10,
            "status": "late",
            "synced_at": "now",
        }
    ]

    assert repo.upsert_timelogs(timelogs) == 1
    assert repo.upsert_milestones(milestones) == 1
    assert repo.list_timelogs("zoagency.teamwork.com")[0]["timelog_id"] == 9
    assert repo.list_milestones("zoagency.teamwork.com")[0]["milestone_id"] == 3


def test_upsert_capacity_snapshots_uses_site_day_person_as_the_conflict_key(client):
    count = repo.upsert_capacity_snapshots(
        "zo",
        "2026-08-25",
        [{"person_id": "42", "logged_minutes": 2_040, "capacity_minutes": 2_400}],
    )

    assert count == 1
    op, table, payload, conflict = client.calls[-1]
    assert op == "upsert"
    assert table == "teamwork_capacity_snapshots"
    assert conflict == "site_id,as_of,person_id"
    assert payload == [
        {
            "site_id": "zo",
            "as_of": "2026-08-25",
            "person_id": "42",
            "logged_minutes": 2_040,
            "capacity_minutes": 2_400,
        }
    ]


def test_list_capacity_snapshots_filters_by_site_and_optional_start_date(client):
    client.store["teamwork_capacity_snapshots"] = [
        {"site_id": "zo", "as_of": "2026-08-25", "person_id": "42"},
        {"site_id": "zo", "as_of": "2026-07-31", "person_id": "41"},
        {"site_id": "other", "as_of": "2026-08-25", "person_id": "43"},
    ]

    rows = repo.list_capacity_snapshots("zo", since="2026-08-01")

    assert rows == [{"site_id": "zo", "as_of": "2026-08-25", "person_id": "42"}]


def test_panel_cache_round_trip(client):
    repo.upsert_panel_cache(
        "zoagency.teamwork.com",
        {"summary": {"project_count": 3}},
        "2026-08-18",
        "2026-08-18T00:00:00+00:00",
    )

    row = repo.get_panel_cache("zoagency.teamwork.com")
    assert row["payload"]["summary"]["project_count"] == 3


def test_sync_state_and_runs_round_trip(client):
    repo.upsert_sync_state("zoagency.teamwork.com", {"last_mode": "nightly"})
    assert repo.get_sync_state("zoagency.teamwork.com")["last_mode"] == "nightly"

    client.store["teamwork_sync_runs"] = [{"id": "run-1", "status": "running"}]
    assert repo.insert_sync_run({"id": "run-1", "status": "running"}) == "run-1"
    repo.finish_sync_run("run-1", "success", entities_upserted={"projects": 2})
    assert client.store["teamwork_sync_runs"][0]["status"] == "success"


def test_lease_conflict_and_expired_takeover(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(
        repo,
        "get_sync_state",
        lambda _site: {"lease_owner": "other", "lease_expires_at": future},
    )
    assert repo.try_acquire_lease("zoagency.teamwork.com", "me") is False

    written = {}
    monkeypatch.setattr(
        repo,
        "get_sync_state",
        lambda _site: {
            "lease_owner": "other",
            "lease_expires_at": "2020-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(repo, "upsert_sync_state", lambda _site, fields: written.update(fields))
    assert repo.try_acquire_lease("zoagency.teamwork.com", "me", ttl_seconds=60) is True
    assert written["lease_owner"] == "me"


def test_release_lease_only_clears_matching_owner(monkeypatch):
    writes = []
    monkeypatch.setattr(repo, "upsert_sync_state", lambda _site, fields: writes.append(fields))
    monkeypatch.setattr(repo, "get_sync_state", lambda _site: {"lease_owner": "other"})
    repo.release_lease("zoagency.teamwork.com", "me")
    assert writes == []
    monkeypatch.setattr(repo, "get_sync_state", lambda _site: {"lease_owner": "me"})
    repo.release_lease("zoagency.teamwork.com", "me")
    assert writes == [{"lease_owner": None, "lease_expires_at": None}]
