from unittest.mock import MagicMock

from app.financial import client_map_repository as repo


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

    def ilike(self, col, val):
        self.calls.append(("ilike", col, val))
        return self

    def or_(self, expr):
        self.calls.append(("or_", expr))
        return self

    def order(self, *args, **_k):
        self.calls.append(("order", *args))
        return self

    def limit(self, n):
        self.calls.append(("limit", n))
        return self

    def insert(self, row):
        self.calls.append(("insert", row))
        return self

    def update(self, row):
        self.calls.append(("update", row))
        return self

    def upsert(self, row, on_conflict=None):
        self.calls.append(("upsert", row, on_conflict))
        return self

    def delete(self):
        self.calls.append(("delete",))
        return self

    def execute(self):
        return MagicMock(data=self.data)


class FakeClient:
    def __init__(self, query: FakeQuery):
        self._query = query

    def table(self, name):
        assert name in (
            "client_map",
            "client_map_job_override",
            "agency_invoice_resolution",
        )
        self._query.calls.append(("table", name))
        return self._query


def test_list_client_map_filters_confidence(monkeypatch):
    q = FakeQuery(data=[{"id": "a", "tag_code": "MVH"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    rows = repo.list_client_map(confidence="suggested")
    assert rows[0]["tag_code"] == "MVH"
    assert ("eq", "link_confidence", "suggested") in q.calls


def test_insert_client_map_sets_unmatched_default(monkeypatch):
    q = FakeQuery(data=[{"id": "x", "link_confidence": "unmatched"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    row = repo.insert_client_map({"tag_code": "MVH", "client_name": "Mountain View Heating"})
    assert row["link_confidence"] == "unmatched"
    assert q.calls[0] == ("table", "client_map")
    insert_call = next(c for c in q.calls if c[0] == "insert")
    assert insert_call[1]["link_confidence"] == "unmatched"


def test_get_job_override_filters_site_and_project(monkeypatch):
    q = FakeQuery(data=[{"id": "o1", "site_id": "zo", "project_id": 42}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    row = repo.get_job_override("zo", 42)
    assert row["project_id"] == 42
    assert ("table", "client_map_job_override") in q.calls
    assert ("eq", "site_id", "zo") in q.calls
    assert ("eq", "project_id", 42) in q.calls


def test_upsert_job_override_uses_site_project_conflict(monkeypatch):
    q = FakeQuery(data=[{"id": "o1", "site_id": "zo", "project_id": 42}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    payload = {"site_id": "zo", "project_id": 42, "client_map_id": "cm-1"}
    row = repo.upsert_job_override(payload)
    assert row["project_id"] == 42
    assert any(
        call[0] == "upsert" and call[2] == "site_id,project_id"
        for call in q.calls
    )


def test_list_invoice_resolutions_filters_realm(monkeypatch):
    q = FakeQuery(data=[{"realm_id": "realm-1", "invoice_id": "invoice-1"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    rows = repo.list_invoice_resolutions("realm-1")
    assert rows[0]["invoice_id"] == "invoice-1"
    assert ("table", "agency_invoice_resolution") in q.calls
    assert ("eq", "realm_id", "realm-1") in q.calls
    assert ("order", "invoice_id") in q.calls


def test_upsert_invoice_resolution_uses_realm_invoice_conflict(monkeypatch):
    q = FakeQuery(data=[{"realm_id": "realm-1", "invoice_id": "invoice-1"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    payload = {
        "realm_id": "realm-1",
        "invoice_id": "invoice-1",
        "resolution": "linked",
        "project_id": 42,
    }
    row = repo.upsert_invoice_resolution(payload)
    assert row["invoice_id"] == "invoice-1"
    upsert_call = next(call for call in q.calls if call[0] == "upsert")
    assert upsert_call[1]["realm_id"] == "realm-1"
    assert upsert_call[1]["invoice_id"] == "invoice-1"
    assert upsert_call[1]["resolution"] == "linked"
    assert upsert_call[1]["project_id"] == 42
    assert upsert_call[1]["updated_at"]
    assert upsert_call[2] == "realm_id,invoice_id"
