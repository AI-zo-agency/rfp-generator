import logging
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.financial import qb_sync
from app.financial import qb_panels_from_db as panels
from app.financial.qb_map import params_hash


def test_auto_backfill_when_not_completed(monkeypatch):
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(qb_sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(qb_sync, "get_sync_state", lambda rid: {"backfill_completed_at": None})
    called = {}
    monkeypatch.setattr(qb_sync, "_run_backfill", lambda **k: called.setdefault("backfill", True))
    monkeypatch.setattr(qb_sync, "_run_nightly", lambda **k: called.setdefault("nightly", True))
    monkeypatch.setattr(qb_sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(qb_sync, "finish_sync_run", lambda *a, **k: None)
    monkeypatch.setattr(qb_sync.settings, "quickbooks_realm_id", "realm-1")
    qb_sync.run_sync("auto")
    assert "backfill" in called
    assert "nightly" not in called


def test_auto_nightly_when_backfill_completed(monkeypatch):
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(qb_sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(
        qb_sync,
        "get_sync_state",
        lambda rid: {"backfill_completed_at": "2026-08-01T00:00:00+00:00"},
    )
    called = {}
    monkeypatch.setattr(qb_sync, "_run_backfill", lambda **k: called.setdefault("backfill", True))
    monkeypatch.setattr(qb_sync, "_run_nightly", lambda **k: called.setdefault("nightly", True))
    monkeypatch.setattr(qb_sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(qb_sync, "finish_sync_run", lambda *a, **k: None)
    monkeypatch.setattr(qb_sync.settings, "quickbooks_realm_id", "realm-1")
    qb_sync.run_sync("auto")
    assert "nightly" in called
    assert "backfill" not in called


def test_failed_nightly_does_not_advance_cursor(monkeypatch):
    state = {"cdc_cursor": "2026-08-01T00:00:00+00:00", "backfill_completed_at": "x"}
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *a, **k: True)
    monkeypatch.setattr(qb_sync, "release_lease", lambda *a, **k: None)
    monkeypatch.setattr(qb_sync, "get_sync_state", lambda rid: state)
    monkeypatch.setattr(qb_sync, "cdc_records", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    advanced = []
    monkeypatch.setattr(qb_sync, "upsert_sync_state", lambda rid, fields: advanced.append(fields))
    monkeypatch.setattr(qb_sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(qb_sync, "finish_sync_run", lambda *a, **k: None)
    monkeypatch.setattr(qb_sync, "upsert_panel_cache", lambda *a, **k: advanced.append({"cache": True}))
    monkeypatch.setattr(qb_sync.settings, "quickbooks_realm_id", "realm-1")
    try:
        qb_sync.run_sync("nightly")
    except RuntimeError:
        pass
    assert not any("cdc_cursor" in (f or {}) for f in advanced if isinstance(f, dict) and "cdc_cursor" in f)
    assert not any(isinstance(f, dict) and f.get("cache") for f in advanced)


def test_failed_backfill_panel_build_publishes_no_cache_or_cursor(monkeypatch):
    published = []
    monkeypatch.setattr(qb_sync, "CDC_ENTITIES", [])
    monkeypatch.setattr(qb_sync, "get_sync_state", lambda _rid: {})
    monkeypatch.setattr(qb_sync, "_backfill_years", lambda started: [2024, 2025])
    monkeypatch.setattr(qb_sync, "_ingest_reports", lambda *args, **kwargs: 0)
    monkeypatch.setattr(qb_sync, "_ingest_company_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        qb_sync,
        "build_overview",
        lambda realm_id, year, **kwargs: (
            {"year": year}
            if year == 2024
            else (_ for _ in ()).throw(RuntimeError("panel build failed"))
        ),
    )
    monkeypatch.setattr(
        qb_sync,
        "upsert_panel_cache",
        lambda *args, **kwargs: published.append(("cache", args)),
    )
    monkeypatch.setattr(
        qb_sync,
        "upsert_sync_state",
        lambda realm_id, fields: published.append(("state", fields)),
    )

    try:
        qb_sync._run_backfill(
            realm_id="realm-1",
            started=datetime(2026, 8, 13, tzinfo=timezone.utc),
            run_id="run-1",
            owner="owner-1",
        )
    except RuntimeError:
        pass

    assert not any(kind == "cache" for kind, _ in published)
    assert not any(
        "cdc_cursor" in fields or "backfill_completed_at" in fields
        for kind, fields in published
        if kind == "state"
    )


def test_backfill_stops_when_lease_renewal_fails(monkeypatch):
    monkeypatch.setattr(qb_sync, "get_backfill_progress", lambda *args: None)
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *args: False)
    queried = []
    monkeypatch.setattr(
        qb_sync,
        "query_page",
        lambda *args, **kwargs: queried.append(True),
    )

    try:
        qb_sync._backfill_entity("realm-1", "Invoice", "synced-at", "owner-1")
        assert False, "expected LeaseHeld"
    except qb_sync.LeaseHeld:
        pass

    assert queried == []


def test_finish_failure_is_logged_as_failed(monkeypatch, caplog):
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *args: True)
    monkeypatch.setattr(qb_sync, "release_lease", lambda *args: None)
    monkeypatch.setattr(qb_sync, "get_sync_state", lambda *args: {})
    monkeypatch.setattr(qb_sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(qb_sync, "_run_backfill", lambda **kwargs: {})
    monkeypatch.setattr(
        qb_sync,
        "finish_sync_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("finish failed")),
    )
    monkeypatch.setattr(qb_sync, "upsert_sync_state", lambda *args: None)
    monkeypatch.setattr(qb_sync.settings, "quickbooks_realm_id", "realm-1")

    with caplog.at_level(logging.ERROR, logger=qb_sync.__name__):
        try:
            qb_sync.run_sync("backfill")
        except RuntimeError:
            pass

    assert any("status=failed" in record.message for record in caplog.records)


def test_lease_busy_raises_lease_error():
    from app.financial.qb_sync import LeaseHeld, run_sync
    with patch("app.financial.qb_sync.try_acquire_lease", return_value=False):
        try:
            run_sync("nightly")
            assert False, "expected LeaseHeld"
        except LeaseHeld:
            pass


def test_report_params_match_panel_snapshot_hashes():
    year = 2026
    as_of = date(2026, 8, 13)
    used = []

    def capture_snapshot(realm_id, report_name, snapshot_year, params):
        used.append((report_name, params_hash(params)))
        return {}

    with patch.object(panels, "_snapshot_payload", side_effect=capture_snapshot):
        panels.revenue_by_class("realm-1", year)
        panels.by_account_manager("realm-1", year)
        panels.client_profitability("realm-1", year)
        panels.monthly_trend("realm-1", year)
        panels.aged_ar_detail("realm-1", year, as_of=as_of)
        panels.expenses_by_vendor("realm-1", year)
        panels.sales_by_customer("realm-1", year)
        panels.liquidity("realm-1", year, as_of=as_of)

    synced = [
        (report_name, params_hash(params))
        for report_name, params in qb_sync.report_jobs(year, as_of)
    ]
    assert sorted(used) == sorted(synced)


def test_as_of_report_hashes_are_stable_across_days():
    hashes_a = [
        (name, params_hash(params))
        for name, params in qb_sync.report_jobs(2026, date(2026, 8, 13))
    ]
    hashes_b = [
        (name, params_hash(params))
        for name, params in qb_sync.report_jobs(2026, date(2026, 8, 14))
    ]
    assert hashes_a == hashes_b
    aged = dict(hashes_a)["AgedReceivableDetail"]
    assert aged == params_hash({"report_date": "2026-12-31"})


def test_ingest_historical_year_fetches_year_end_not_today(monkeypatch):
    fetched = []
    monkeypatch.setattr(
        qb_sync,
        "report",
        lambda name, **params: fetched.append((name, params)) or {},
    )
    monkeypatch.setattr(qb_sync, "upsert_report_snapshot", lambda row: None)

    qb_sync._ingest_reports("r1", [2024], date(2026, 8, 13), "now")

    by_name = {name: params for name, params in fetched}
    assert by_name["AgedReceivableDetail"]["report_date"] == "2024-12-31"
    assert by_name["BalanceSheet"]["date"] == "2024-12-31"
    assert by_name["CashFlow"]["end_date"] == "2024-12-31"


def test_ingest_current_year_fetches_as_of_but_hashes_year_end(monkeypatch):
    fetched = []
    snapshots = []
    monkeypatch.setattr(
        qb_sync,
        "report",
        lambda name, **params: fetched.append((name, params)) or {},
    )
    monkeypatch.setattr(qb_sync, "upsert_report_snapshot", lambda row: snapshots.append(row))

    qb_sync._ingest_reports("r1", [2026], date(2026, 8, 13), "now")

    by_fetch = {name: params for name, params in fetched}
    assert by_fetch["AgedReceivableDetail"]["report_date"] == "2026-08-13"
    assert by_fetch["BalanceSheet"]["date"] == "2026-08-13"
    assert by_fetch["CashFlow"]["end_date"] == "2026-08-13"

    by_snap = {row["report_name"]: row for row in snapshots}
    assert by_snap["AgedReceivableDetail"]["params_hash"] == params_hash(
        {"report_date": "2026-12-31"}
    )
    assert by_snap["BalanceSheet"]["params_hash"] == params_hash({"date": "2026-12-31"})
    assert by_snap["CashFlow"]["params_hash"] == params_hash(
        {"start_date": "2026-01-01", "end_date": "2026-12-31"}
    )


def _stub_backfill_ingest(monkeypatch) -> None:
    monkeypatch.setattr(qb_sync, "_ingest_reports", lambda *args, **kwargs: 0)
    monkeypatch.setattr(qb_sync, "_ingest_company_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "_write_panel_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr(qb_sync, "upsert_entities", lambda *args, **kwargs: 0)
    monkeypatch.setattr(qb_sync, "upsert_backfill_progress", lambda *args: None)


def test_explicit_backfill_reruns_when_already_completed(monkeypatch):
    state = {"backfill_completed_at": "2026-08-01T00:00:00+00:00"}
    progress = {"Invoice": {"completed": True, "startposition": 5001}}
    queried = []

    monkeypatch.setattr(qb_sync, "CDC_ENTITIES", ["Invoice"])
    monkeypatch.setattr(qb_sync, "get_sync_state", lambda _rid: state)
    monkeypatch.setattr(
        qb_sync,
        "get_backfill_progress",
        lambda _rid, entity: progress.get(entity),
    )
    monkeypatch.setattr(
        qb_sync,
        "clear_backfill_progress",
        lambda _rid: progress.clear(),
        raising=False,
    )
    monkeypatch.setattr(
        qb_sync,
        "upsert_sync_state",
        lambda _rid, fields: state.update(fields),
    )
    monkeypatch.setattr(
        qb_sync,
        "query_page",
        lambda sql, entity, startposition=1: queried.append(startposition) or [],
    )
    _stub_backfill_ingest(monkeypatch)

    qb_sync._run_backfill(
        realm_id="r1",
        started=datetime(2026, 8, 13, tzinfo=timezone.utc),
        run_id="run-1",
        owner="owner-1",
    )

    assert queried == [1]


def test_in_progress_backfill_resumes_without_clearing(monkeypatch):
    state = {"backfill_completed_at": None}
    progress = {
        "Invoice": {"completed": True, "startposition": 5001},
        "Bill": {"completed": False, "startposition": 1001},
    }
    queried = []
    cleared = []

    monkeypatch.setattr(qb_sync, "CDC_ENTITIES", ["Invoice", "Bill"])
    monkeypatch.setattr(qb_sync, "get_sync_state", lambda _rid: state)
    monkeypatch.setattr(
        qb_sync,
        "get_backfill_progress",
        lambda _rid, entity: progress.get(entity),
    )
    monkeypatch.setattr(
        qb_sync,
        "clear_backfill_progress",
        lambda _rid: cleared.append(_rid),
        raising=False,
    )
    monkeypatch.setattr(qb_sync, "upsert_sync_state", lambda _rid, fields: None)
    monkeypatch.setattr(
        qb_sync,
        "query_page",
        lambda sql, entity, startposition=1: queried.append((entity, startposition)) or [],
    )
    _stub_backfill_ingest(monkeypatch)

    qb_sync._run_backfill(
        realm_id="r1",
        started=datetime(2026, 8, 13, tzinfo=timezone.utc),
        run_id="run-1",
        owner="owner-1",
    )

    assert cleared == []
    assert queried == [("Bill", 1001)]


def test_backfill_query_orders_by_stable_id(monkeypatch):
    sqls = []
    monkeypatch.setattr(qb_sync, "get_backfill_progress", lambda *args: None)
    monkeypatch.setattr(qb_sync, "try_acquire_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        qb_sync,
        "query_page",
        lambda sql, entity, startposition=1: sqls.append(sql) or [],
    )
    monkeypatch.setattr(qb_sync, "upsert_backfill_progress", lambda *args: None)
    monkeypatch.setattr(qb_sync, "upsert_entities", lambda *args, **kwargs: 0)

    qb_sync._backfill_entity("r1", "Invoice", "now", "owner-1")
    qb_sync._backfill_entity("r1", "Customer", "now", "owner-1")

    invoice_sql, customer_sql = sqls
    assert "id" in invoice_sql.lower()
    assert "order" in invoice_sql.lower()
    assert "id" in customer_sql.lower()
    assert "order" in customer_sql.lower()


def test_nightly_lease_failure_before_cdc_skips_cdc(monkeypatch):
    acquires = {"n": 0}
    cdc_called = []

    def acquire(*_args, **_kwargs):
        acquires["n"] += 1
        return acquires["n"] == 1

    monkeypatch.setattr(qb_sync, "try_acquire_lease", acquire)
    monkeypatch.setattr(qb_sync, "release_lease", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        qb_sync,
        "get_sync_state",
        lambda _rid: {"cdc_cursor": "2026-08-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        qb_sync,
        "cdc_records",
        lambda *args, **kwargs: cdc_called.append(True) or {},
    )
    monkeypatch.setattr(qb_sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(qb_sync, "finish_sync_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "upsert_sync_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "_ingest_reports", lambda *args, **kwargs: 0)
    monkeypatch.setattr(qb_sync, "_ingest_company_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "_write_panel_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync.settings, "quickbooks_realm_id", "realm-1")

    try:
        qb_sync.run_sync("nightly")
        assert False, "expected LeaseHeld"
    except qb_sync.LeaseHeld:
        pass

    assert cdc_called == []


def test_nightly_lease_failure_before_reports_skips_ingest(monkeypatch):
    allow = {"ok": True}
    ingested = []

    def acquire(*_args, **_kwargs):
        return allow["ok"]

    def cdc(*_args, **_kwargs):
        allow["ok"] = False
        return {}

    monkeypatch.setattr(qb_sync, "try_acquire_lease", acquire)
    monkeypatch.setattr(qb_sync, "release_lease", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        qb_sync,
        "get_sync_state",
        lambda _rid: {"cdc_cursor": "2026-08-01T00:00:00+00:00"},
    )
    monkeypatch.setattr(qb_sync, "cdc_records", cdc)
    monkeypatch.setattr(qb_sync, "upsert_entities", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        qb_sync,
        "_ingest_reports",
        lambda *args, **kwargs: ingested.append(True) or 0,
    )
    monkeypatch.setattr(qb_sync, "_ingest_company_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "_write_panel_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "insert_sync_run", lambda row: "run-1")
    monkeypatch.setattr(qb_sync, "finish_sync_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync, "upsert_sync_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(qb_sync.settings, "quickbooks_realm_id", "realm-1")

    try:
        qb_sync.run_sync("nightly")
        assert False, "expected LeaseHeld"
    except qb_sync.LeaseHeld:
        pass

    assert ingested == []
