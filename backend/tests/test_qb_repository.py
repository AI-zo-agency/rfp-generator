from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.financial import qb_repository as repo


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

    def order(self, *_args, **_kwargs):
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
        page_range = getattr(self, "_range", None)
        if page_range is not None:
            start, end = page_range
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


def test_upsert_entities_maps_invoice_and_batches_at_500(client):
    payloads = [{"Id": str(index), "TotalAmt": index} for index in range(501)]

    assert repo.upsert_entities("r1", "Invoice", payloads, synced_at="now") == 501

    calls = [call for call in client.calls if call[1] == "qb_invoices"]
    assert [len(call[2]) for call in calls] == [500, 1]
    assert client.store["qb_invoices"][0]["qbo_id"] == "0"


def test_deleted_invoice_is_tombstoned(client):
    repo.upsert_entities(
        "r1", "Invoice", [{"Id": "i1", "status": "Deleted"}], synced_at="now"
    )

    assert client.store["qb_invoices"][0]["is_deleted"] is True


def test_purchase_replaces_children_and_deleted_purchase_only_clears(client):
    active = {
        "Id": "p1",
        "Line": [{"Id": "l1", "Amount": 12}],
    }
    repo.upsert_entities("r1", "Purchase", [active], synced_at="now")
    assert client.store["qb_purchase_lines"][0]["purchase_id"] == "p1"
    assert any(call[:2] == ("delete", "qb_purchase_lines") for call in client.calls)

    repo.upsert_entities(
        "r1", "Purchase", [{"Id": "p1", "status": "Deleted"}], synced_at="later"
    )
    assert client.store["qb_purchase_lines"] == []


def test_payment_replaces_links(client):
    payment = {
        "Id": "pay1",
        "Line": [{"Amount": 5, "LinkedTxn": [{"TxnType": "Invoice", "TxnId": "i1"}]}],
    }
    repo.upsert_entities("r1", "Payment", [payment], synced_at="now")
    assert client.store["qb_txn_links"][0]["from_id"] == "pay1"


def test_lease_conflict_and_expired_takeover(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(
        repo,
        "get_sync_state",
        lambda _rid: {"lease_owner": "other", "lease_expires_at": future},
    )
    assert repo.try_acquire_lease("r1", "me") is False

    written = {}
    monkeypatch.setattr(
        repo,
        "get_sync_state",
        lambda _rid: {
            "lease_owner": "other",
            "lease_expires_at": "2020-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        repo, "upsert_sync_state", lambda _rid, fields: written.update(fields)
    )
    assert repo.try_acquire_lease("r1", "me", ttl_seconds=60) is True
    assert written["lease_owner"] == "me"


def test_release_lease_only_clears_matching_owner(monkeypatch):
    writes = []
    monkeypatch.setattr(repo, "upsert_sync_state", lambda rid, fields: writes.append(fields))
    monkeypatch.setattr(repo, "get_sync_state", lambda rid: {"lease_owner": "other"})
    repo.release_lease("r1", "me")
    assert writes == []
    monkeypatch.setattr(repo, "get_sync_state", lambda rid: {"lease_owner": "me"})
    repo.release_lease("r1", "me")
    assert writes == [{"lease_owner": None, "lease_expires_at": None}]


def test_panel_cache_round_trip(client):
    repo.upsert_panel_cache("r1", 2026, {"revenue": 12}, "2026-08-13", "now")

    assert repo.get_panel_cache("r1", 2026)["payload"] == {"revenue": 12}


def test_control_and_snapshot_repository_operations(client):
    repo.upsert_sync_state("r1", {"last_mode": "nightly"})
    assert repo.get_sync_state("r1")["last_mode"] == "nightly"
    repo.upsert_backfill_progress("r1", "Invoice", 1001, False)
    assert repo.get_backfill_progress("r1", "Invoice")["startposition"] == 1001
    repo.upsert_report_snapshot(
        {
            "realm_id": "r1",
            "report_name": "ProfitAndLoss",
            "year": 2026,
            "params_hash": "h",
        }
    )
    assert repo.get_report_snapshot("r1", "ProfitAndLoss", 2026, "h") is not None
    repo.upsert_company_info({"realm_id": "r1", "company_name": "Zo"})
    assert client.store["qb_company_info"][0]["company_name"] == "Zo"


def test_sync_run_insert_and_finish(client):
    client.store["qb_sync_runs"] = [{"id": "run1", "status": "running"}]
    assert repo.insert_sync_run({"id": "run1", "status": "running"}) == "run1"
    repo.finish_sync_run("run1", "success", entities_upserted={"Invoice": 2})
    assert client.store["qb_sync_runs"][0]["status"] == "success"
    assert client.store["qb_sync_runs"][0]["finished_at"]


@pytest.mark.parametrize(
    ("function_name", "table"),
    [
        ("list_invoices", "qb_invoices"),
        ("list_bills", "qb_bills"),
        ("list_payments", "qb_payments"),
        ("list_purchases", "qb_purchases"),
        ("list_purchase_lines", "qb_purchase_lines"),
        ("list_txn_links", "qb_txn_links"),
        ("list_purchase_orders", "qb_purchase_orders"),
        ("list_bill_payments", "qb_bill_payments"),
        ("list_credit_memos", "qb_credit_memos"),
        ("list_customers", "qb_customers"),
        ("list_classes", "qb_classes"),
        ("list_departments", "qb_departments"),
    ],
)
def test_list_helpers_scope_to_realm_and_apply_filters(client, function_name, table):
    client.store[table] = [
        {"realm_id": "r1", "qbo_id": "1", "active": True},
        {"realm_id": "r1", "qbo_id": "2", "active": False},
        {"realm_id": "r2", "qbo_id": "3", "active": True},
    ]

    rows = getattr(repo, function_name)("r1", active=True)

    assert [row["qbo_id"] for row in rows] == ["1"]


def test_list_rows_paginates_past_postgrest_1000_cap(monkeypatch):
    class PagingQuery:
        def __init__(self, client):
            self.client = client
            self._range = None

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def range(self, start, end):
            self.client.ranges.append((start, end))
            self._range = (start, end)
            return self

        def execute(self):
            if self._range is None:
                return FakeResponse([{"qbo_id": str(i)} for i in range(1000)])
            start, _end = self._range
            if start == 0:
                return FakeResponse([{"qbo_id": str(i)} for i in range(1000)])
            return FakeResponse([{"qbo_id": str(i)} for i in range(1000, 1003)])

    class PagingClient:
        def __init__(self):
            self.ranges = []

        def table(self, _name):
            return PagingQuery(self)

    fake = PagingClient()
    monkeypatch.setattr(repo, "_get_client", lambda: fake)

    rows = repo.list_invoices("r1")

    assert len(rows) == 1003
    assert fake.ranges[0] == (0, 999)
    assert fake.ranges[1] == (1000, 1999)
