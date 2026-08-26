"""Agency overview: confirmed money only."""

from __future__ import annotations

from app.financial import agency_overview
from app.financial.agency_overview import (
    billed_without_live_project,
    build_job_row,
    money_by_customer_id,
    unlinked_invoices,
)
from app.financial.client_map import Ambiguous, ClientMatch


def test_money_by_customer_id_sums_billed_and_open_ar():
    invoices = [
        {"customer_id": "9", "customer_name": "Hampton", "total_amt": 1000, "is_deleted": False},
        {"customer_id": "9", "customer_name": "Hampton", "total_amt": 250, "is_deleted": False},
        {"customer_id": "2", "customer_name": "Other", "total_amt": 50, "is_deleted": False},
    ]
    open_invoices = [
        {"customer_id": "9", "customer_name": "Hampton", "balance": 400, "is_deleted": False},
        {"customer_id": "2", "customer_name": "Other", "balance": 10, "is_deleted": False},
    ]

    money = money_by_customer_id(
        "realm",
        2026,
        invoices=invoices,
        open_invoices=open_invoices,
    )

    assert money["9"]["billed_ytd"] == 1250
    assert money["9"]["open_ar"] == 400
    assert money["2"]["billed_ytd"] == 50


def test_suggested_match_gets_hours_but_no_money():
    money = {
        "55": {"customer_id": "55", "customer_name": "X", "billed_ytd": 900, "open_ar": 100}
    }
    match = ClientMatch(
        client_map_id="c1",
        tag_code="EFF",
        client_name="EverFast",
        qb_customer_ids=["55"],
        link_confidence="suggested",
        via="tag",
    )
    row = build_job_row(
        {"id": "1", "name": "EFF 26132 Retainer", "company_name": "Everfast", "status": "current", "health": "ok"},
        match=match,
        hours_mtd_minutes=120,
        money=money,
    )
    assert row["hours_mtd_minutes"] == 120
    assert row["billed_ytd"] is None
    assert row["open_ar"] is None
    assert row["join"] == "suggested"


def test_suggested_job_override_is_not_mapped_or_monetized():
    match = ClientMatch(
        client_map_id="c1",
        qb_customer_ids=["55"],
        link_confidence="suggested",
        via="override",
    )
    row = build_job_row(
        {"id": "1", "name": "EFF 26132 Retainer"},
        match=match,
        hours_mtd_minutes=120,
        money={"55": {"customer_id": "55", "customer_name": "X", "billed_ytd": 900, "open_ar": 100}},
    )

    assert row["join"] == "suggested"
    assert row["billed_ytd"] is None
    assert row["open_ar"] is None


def test_confirmed_match_gets_money():
    money = {
        "55": {"customer_id": "55", "customer_name": "X", "billed_ytd": 900, "open_ar": 100}
    }
    match = ClientMatch(
        client_map_id="c1",
        tag_code="EFF",
        client_name="EverFast",
        qb_customer_ids=["55"],
        link_confidence="confirmed",
        via="tag",
    )
    row = build_job_row(
        {"id": "1", "name": "EFF 26132 Retainer", "company_name": "Everfast", "status": "late", "health": "bad"},
        match=match,
        hours_mtd_minutes=30,
        money=money,
    )
    assert row["billed_ytd"] == 900
    assert row["open_ar"] == 100
    assert row["join"] == "confirmed"
    assert row["status"] == "late"


def test_ambiguous_has_no_money():
    row = build_job_row(
        {"id": "1", "name": "ATC 25001 x", "company_name": "Arctic", "status": "current", "health": "ok"},
        match=Ambiguous(tag_code="ATC", candidates=("a", "b")),
        hours_mtd_minutes=0,
        money={"1": {"customer_id": "1", "customer_name": "A", "billed_ytd": 10, "open_ar": 1}},
    )
    assert row["join"] == "ambiguous"
    assert row["billed_ytd"] is None


def test_billed_without_live_project_lists_orphans():
    money = {
        "1": {"customer_id": "1", "customer_name": "Mapped", "billed_ytd": 500, "open_ar": 0},
        "2": {"customer_id": "2", "customer_name": "Orphan", "billed_ytd": 1200, "open_ar": 50},
    }
    rows = billed_without_live_project(money, linked_customer_ids={"1"})
    assert len(rows) == 1
    assert rows[0]["customer_id"] == "2"
    assert rows[0]["billed_ytd"] == 1200


def test_unlinked_invoices_skips_resolved_deleted_and_missing_ids_then_sorts():
    rows = unlinked_invoices(
        [
            {"qbo_id": "linked", "balance": 900, "total_amt": 900},
            {"qbo_id": "internal", "balance": 800, "total_amt": 800},
            {"qbo_id": "deleted", "is_deleted": True, "balance": 700, "total_amt": 700},
            {"balance": 600, "total_amt": 600},
            {"qbo_id": "small", "doc_number": "INV-1", "customer_id": "1", "customer_name": "Small", "txn_date": "2026-01-01", "due_date": "2026-01-31", "total_amt": 200, "balance": 50},
            {"qbo_id": "first", "total_amt": 300, "balance": 100},
            {"qbo_id": "second", "total_amt": 400, "balance": 100},
        ],
        resolutions=[
            {"invoice_id": "linked", "resolution": "linked"},
            {"invoice_id": "internal", "resolution": "internal"},
        ],
    )

    assert [row["invoice_id"] for row in rows] == ["second", "first", "small"]
    assert rows[-1] == {
        "invoice_id": "small",
        "invoice_number": "INV-1",
        "customer_id": "1",
        "customer_name": "Small",
        "txn_date": "2026-01-01",
        "due_date": "2026-01-31",
        "total_amt": 200.0,
        "open_ar": 50.0,
        "status": "open",
    }


def test_unlinked_invoices_uses_supplied_status_and_balance_fallbacks():
    rows = unlinked_invoices(
        [
            {"qbo_id": "supplied", "status": "overdue", "balance": 0},
            {"qbo_id": "open", "balance": 12.5},
            {"qbo_id": "paid", "status": "", "balance": 0},
        ],
        resolutions=[],
    )

    assert {row["invoice_id"]: row["status"] for row in rows} == {
        "supplied": "overdue",
        "open": "open",
        "paid": "paid",
    }


def test_overview_reuses_ytd_invoices_and_caps_invoice_exceptions(monkeypatch):
    invoices = [
        {"qbo_id": str(index), "total_amt": index, "balance": index}
        for index in range(41)
    ]
    invoice_calls = []
    money_calls = []
    monkeypatch.setattr(agency_overview, "_site_id", lambda: "site-1")
    monkeypatch.setattr(
        agency_overview,
        "overview_from_cache",
        lambda: {
            "projects": [
                {
                    "id": "44",
                    "name": "ACM 26001 Retainer",
                    "company_name": "Acme",
                }
            ],
            "summary": {"project_count": 1},
        },
    )
    monkeypatch.setattr(agency_overview.map_repo, "list_client_map", lambda: [])
    monkeypatch.setattr(agency_overview.map_repo, "list_job_overrides", lambda **_kwargs: [])
    monkeypatch.setattr(agency_overview.map_repo, "list_invoice_resolutions", lambda _realm_id: [])
    monkeypatch.setattr(
        agency_overview,
        "list_invoices",
        lambda *args, **kwargs: invoice_calls.append((args, kwargs)) or invoices,
    )
    monkeypatch.setattr(
        agency_overview,
        "money_by_customer_id",
        lambda *args, **kwargs: money_calls.append((args, kwargs)) or {},
    )
    monkeypatch.setattr(
        agency_overview,
        "resolve_project",
        lambda *_args, **_kwargs: ClientMatch(
            client_map_id="cm-1",
            tag_code="ACM",
            client_name="Acme",
            qb_customer_ids=[],
            link_confidence="confirmed",
            via="tag",
        ),
    )
    monkeypatch.setattr(agency_overview, "pl_summary", lambda *_args: {"income": 0})

    payload = agency_overview.build_agency_overview(year=2026)

    assert len(invoice_calls) == 1
    assert money_calls[0][1]["invoices"] is invoices
    assert len(payload["unlinked_invoices"]) == 40
    assert payload["unlinked_invoices"][0]["invoice_id"] == "40"
    assert payload["resolution_options"] == [
        {
            "project_id": "44",
            "project_name": "ACM 26001 Retainer",
            "company_name": "Acme",
            "client_map_id": "cm-1",
        }
    ]


def test_overview_skips_malformed_cached_projects_time_and_overrides(monkeypatch):
    monkeypatch.setattr(agency_overview, "_site_id", lambda: "site-1")
    monkeypatch.setattr(
        agency_overview,
        "overview_from_cache",
        lambda: {
            "projects": [None, "not-a-project", {"id": "44", "name": "ACM 26001 Retainer"}],
            "time": {"by_project": [None, "not-a-time-entry", {"id": "44", "minutes": "invalid"}]},
        },
    )
    monkeypatch.setattr(agency_overview.map_repo, "list_client_map", lambda: [])
    monkeypatch.setattr(
        agency_overview.map_repo,
        "list_job_overrides",
        lambda **_kwargs: [None, "not-an-override", {"project_id": "bad"}],
    )
    monkeypatch.setattr(agency_overview.map_repo, "list_invoice_resolutions", lambda _realm_id: [])
    monkeypatch.setattr(agency_overview, "list_invoices", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(agency_overview, "money_by_customer_id", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agency_overview, "pl_summary", lambda *_args: {"income": 0})

    payload = agency_overview.build_agency_overview(year=2026)

    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0]["project_id"] == "44"
    assert payload["jobs"][0]["hours_mtd_minutes"] == 0


def test_overview_does_not_count_suggested_override_as_mapped(monkeypatch):
    monkeypatch.setattr(agency_overview, "_site_id", lambda: "site-1")
    monkeypatch.setattr(
        agency_overview,
        "overview_from_cache",
        lambda: {"projects": [{"id": "44", "name": "ACM 26001 Retainer"}]},
    )
    monkeypatch.setattr(agency_overview.map_repo, "list_client_map", lambda: [])
    monkeypatch.setattr(
        agency_overview.map_repo,
        "list_job_overrides",
        lambda **_kwargs: [{"project_id": 44, "qb_customer_ids": ["55"], "link_confidence": "suggested"}],
    )
    monkeypatch.setattr(agency_overview.map_repo, "list_invoice_resolutions", lambda _realm_id: [])
    monkeypatch.setattr(agency_overview, "list_invoices", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        agency_overview,
        "money_by_customer_id",
        lambda *_args, **_kwargs: {"55": {"customer_id": "55", "customer_name": "Acme", "billed_ytd": 900, "open_ar": 100}},
    )
    monkeypatch.setattr(agency_overview, "pl_summary", lambda *_args: {"income": 0})

    payload = agency_overview.build_agency_overview(year=2026)

    assert payload["jobs"][0]["join"] == "suggested"
    assert payload["jobs"][0]["billed_ytd"] is None
    assert payload["position"]["join_mapped"] == 0
    assert payload["needs_mapping"] == [{
        "project_id": "44",
        "project_name": "ACM 26001 Retainer",
        "company_name": "",
        "join": "suggested",
    }]
