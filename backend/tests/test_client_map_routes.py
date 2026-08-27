from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient

from app.financial import router as fin_router
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _known_invoices(monkeypatch):
    """Resolution requests always operate on a real invoice in the QB realm."""
    monkeypatch.setattr(
        fin_router,
        "list_invoices",
        lambda _realm_id: [
            {"qbo_id": "17", "customer_id": "customer-17"},
            {"qbo_id": "internal-17", "customer_id": "internal-customer"},
        ],
    )


def test_unmatched_teamwork_respects_confirmed_company_names(monkeypatch):
    monkeypatch.setattr(
        fin_router,
        "list_client_map",
        lambda **filters: [
            {
                "id": "hml",
                "link_confidence": "confirmed",
                "qb_customer_ids": ["919"],
                "teamwork_company_ids": [],
                "teamwork_company_names": ["Hampton Lumber"],
            }
        ]
        if filters.get("confidence") == "confirmed"
        else [],
    )
    monkeypatch.setattr(
        fin_router,
        "overview_from_cache",
        lambda: {
            "projects": [
                {"company_id": None, "company_name": "Hampton Lumber"},
                {"company_id": None, "company_name": "Back Office Connection"},
            ]
        },
    )
    monkeypatch.setattr(fin_router, "list_customers", lambda *_a, **_k: [])

    response = client.get("/api/v1/financials/client-map/unmatched")

    assert response.status_code == 200
    body = response.json()
    assert body["teamwork"] == [{"id": None, "name": "Back Office Connection"}]


def test_get_client_map_returns_repository_rows(monkeypatch):
    rows = [{"id": "cm-1", "tag_code": "MVH", "link_confidence": "suggested"}]
    calls = []
    monkeypatch.setattr(
        fin_router,
        "list_client_map",
        lambda **filters: calls.append(filters) or rows,
    )

    response = client.get(
        "/api/v1/financials/client-map",
        params={"confidence": "suggested", "status": "Active", "q": "MVH"},
    )

    assert response.status_code == 200
    assert response.json() == rows
    assert calls == [{"confidence": "suggested", "status": "Active", "q": "MVH"}]


def test_patch_client_map_promotes_suggestion(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fin_router,
        "update_client_map",
        lambda row_id, patch: calls.append((row_id, patch))
        or {"id": row_id, **patch},
    )

    response = client.patch(
        "/api/v1/financials/client-map/cm-1",
        json={"link_confidence": "confirmed"},
    )

    assert response.status_code == 200
    assert calls == [("cm-1", {"link_confidence": "confirmed"})]
    assert response.json()["link_confidence"] == "confirmed"


def test_patch_client_map_reject_clears_qb(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fin_router,
        "update_client_map",
        lambda row_id, patch: calls.append((row_id, patch))
        or {"id": row_id, **patch},
    )
    clears = {
        "qb_customer_ids": [],
        "qb_customer_names": [],
        "link_confidence": "unmatched",
        "link_reason": None,
    }

    response = client.patch(
        "/api/v1/financials/client-map/cm-1",
        json=clears,
    )

    assert response.status_code == 200
    assert calls == [("cm-1", clears)]


def test_post_client_map_link_runs_requested_passes(monkeypatch):
    run_link = AsyncMock(return_value={"confirmed": 2, "suggested": 1})
    monkeypatch.setattr(fin_router, "run_client_map_link", run_link)

    response = client.post(
        "/api/v1/financials/client-map/link",
        json={"include_ai": False},
    )

    assert response.status_code == 200
    assert response.json() == {"confirmed": 2, "suggested": 1}
    run_link.assert_awaited_once_with(include_ai=False)


def test_get_agency_overview_returns_builder_payload(monkeypatch):
    payload = {
        "year": 2026,
        "position": {"booked_ytd": 1000, "open_ar": 50, "live_jobs": 1, "overdue_tasks": 0, "join_mapped": 1, "join_total": 1},
        "jobs": [{"project_id": "1", "join": "confirmed", "billed_ytd": 900, "open_ar": 40}],
        "needs_mapping": [],
        "billed_without_project": [],
    }
    monkeypatch.setattr(fin_router, "build_agency_overview", lambda year=None: payload)

    response = client.get("/api/v1/financials/agency/overview?year=2026")

    assert response.status_code == 200
    assert response.json()["jobs"][0]["open_ar"] == 40


def test_get_agency_overview_rejects_year_outside_sensible_range():
    assert client.get("/api/v1/financials/agency/overview?year=1999").status_code == 422
    assert client.get("/api/v1/financials/agency/overview?year=2101").status_code == 422


def test_invoice_resolution_requires_linked_project_and_rejects_internal_project():
    linked_response = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "linked"},
    )
    internal_response = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "internal", "project_id": "44"},
    )

    assert linked_response.status_code == 422
    assert internal_response.status_code == 422


def test_invoice_resolution_rejects_unknown_project_and_saves_known_project(monkeypatch):
    monkeypatch.setattr(
        fin_router,
        "overview_from_cache",
        lambda: {"projects": [{"id": "44"}]},
    )
    saved_payloads = []
    monkeypatch.setattr(
        fin_router,
        "upsert_invoice_resolution",
        lambda payload: saved_payloads.append(payload) or {"id": "resolution-1", **payload},
    )
    client_map_id = "6aaec310-4b9f-4a61-8e66-b81387bf2097"
    monkeypatch.setattr(
        fin_router,
        "get_client_map_row",
        lambda row_id: {"id": row_id, "qb_customer_ids": ["customer-17"]} if row_id == client_map_id else None,
    )

    unknown = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "linked", "project_id": "not-a-project"},
    )
    known = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "linked", "project_id": "44", "client_map_id": client_map_id},
    )

    assert unknown.status_code == 404
    assert known.status_code == 200
    assert saved_payloads == [{
        "invoice_id": "17",
        "resolution": "linked",
        "project_id": "44",
        "client_map_id": client_map_id,
        "realm_id": fin_router.settings.quickbooks_realm_id,
    }]


def test_invoice_resolution_rejects_missing_invoice_and_mismatched_client_map(monkeypatch):
    client_map_id = "6aaec310-4b9f-4a61-8e66-b81387bf2097"
    monkeypatch.setattr(
        fin_router,
        "overview_from_cache",
        lambda: {"projects": [{"id": "44", "name": "ACM 26001 Retainer"}]},
    )
    monkeypatch.setattr(
        fin_router,
        "get_client_map_row",
        lambda row_id: {"id": row_id, "qb_customer_ids": ["wrong-customer"]} if row_id == client_map_id else None,
    )
    monkeypatch.setattr(fin_router, "list_client_map", lambda: [])

    missing = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "does-not-exist", "resolution": "internal"},
    )
    mismatched = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "linked", "project_id": "44", "client_map_id": client_map_id},
    )

    assert missing.status_code == 404
    assert mismatched.status_code == 422


def test_invoice_resolution_validates_client_map_and_requires_existing_map(monkeypatch):
    client_map_id = "6aaec310-4b9f-4a61-8e66-b81387bf2097"
    monkeypatch.setattr(fin_router, "overview_from_cache", lambda: {"projects": [{"id": "44"}]})
    monkeypatch.setattr(fin_router, "get_client_map_row", lambda _row_id: None)

    malformed = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "linked", "project_id": "44", "client_map_id": "not-a-uuid"},
    )
    unknown = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "linked", "project_id": "44", "client_map_id": client_map_id},
    )
    internal = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "17", "resolution": "internal", "client_map_id": client_map_id},
    )

    assert malformed.status_code == 422
    assert unknown.status_code == 404
    assert internal.status_code == 422


def test_job_override_validates_and_checks_client_map_before_upsert(monkeypatch):
    client_map_id = "6aaec310-4b9f-4a61-8e66-b81387bf2097"
    saved = []
    monkeypatch.setattr(fin_router, "get_client_map_row", lambda row_id: {"id": row_id} if row_id == client_map_id else None)
    monkeypatch.setattr(fin_router, "upsert_job_override", lambda payload: saved.append(payload) or payload)

    malformed = client.post(
        "/api/v1/financials/client-map/job-overrides",
        json={"site_id": "site-1", "project_id": 44, "client_map_id": "not-a-uuid"},
    )
    unknown = client.post(
        "/api/v1/financials/client-map/job-overrides",
        json={"site_id": "site-1", "project_id": 44, "client_map_id": "d3c9ed82-df68-433d-a75c-df6aabb74ae0"},
    )
    known = client.post(
        "/api/v1/financials/client-map/job-overrides",
        json={"site_id": "site-1", "project_id": 44, "client_map_id": client_map_id},
    )

    assert malformed.status_code == 422
    assert unknown.status_code == 404
    assert known.status_code == 200
    assert saved[0]["client_map_id"] == client_map_id


def test_internal_invoice_resolution_uses_settings_realm_and_null_link_fields(monkeypatch):
    monkeypatch.setattr(
        fin_router,
        "settings",
        SimpleNamespace(quickbooks_realm_id="realm-from-settings"),
    )
    saved_payloads = []
    monkeypatch.setattr(
        fin_router,
        "upsert_invoice_resolution",
        lambda payload: saved_payloads.append(payload) or payload,
    )

    response = client.post(
        "/api/v1/financials/agency/invoice-resolutions",
        json={"invoice_id": "internal-17", "resolution": "internal"},
    )

    assert response.status_code == 200
    assert saved_payloads == [{
        "invoice_id": "internal-17",
        "resolution": "internal",
        "project_id": None,
        "client_map_id": None,
        "realm_id": "realm-from-settings",
    }]
