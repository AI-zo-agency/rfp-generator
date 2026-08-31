"""Adversarial edge cases for /api/v1/financials — assert correct hardening."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/api/v1/financials"

# Characters that break the raw PostgREST `.or_(f"...ilike.{needle}...")` filter.
_FILTER_BREAKERS = [
    "' OR 1=1--",
    "a,b",
    "x)eq.true",
    "foo&bar",
]


# ── Inventory smoke: every documented route responds (not 404 route-miss) ─────


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("GET", "/sources", {}),
        ("GET", "/checklist", {}),
        ("GET", "/audit-queue", {}),
        ("GET", "/teamwork/status", {}),
        ("GET", "/teamwork/overview", {}),
        ("GET", "/teamwork/ai-insights", {}),
        ("GET", "/quickbooks/status", {}),
        ("GET", "/quickbooks/overview", {}),
        ("GET", "/quickbooks/ai-insights", {}),
        ("GET", "/agency/overview", {}),
        ("GET", "/agency/ai-insights", {}),
        ("GET", "/client-map", {}),
        ("GET", "/client-map/unmatched", {}),
        ("GET", "/client-map/job-overrides", {}),
        ("GET", "/iworker-timesheets", {}),
        ("POST", "/quickbooks/sync", {"json": {"mode": "auto"}}),
        ("POST", "/teamwork/sync", {"json": {"mode": "auto"}}),
        ("POST", "/agency/ai-insights/snapshot", {}),
        ("POST", "/agency/ai-insights/generate", {}),
    ],
)
def test_route_exists(method, path, kwargs, monkeypatch):
    """Smoke: route is wired. Stub DB-backed handlers so this stays offline-safe."""
    monkeypatch.setattr(
        "app.financial.router.get_teamwork_panel_cache", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router.get_teamwork_sync_state", lambda *_a, **_k: {}, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router.get_panel_cache", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router.get_sync_state", lambda *_a, **_k: {}, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router.build_agency_overview",
        lambda **_k: {"year": 2026, "jobs": [], "position": {}},
        raising=False,
    )
    monkeypatch.setattr("app.financial.router._agency_insight_row", lambda: None, raising=False)
    monkeypatch.setattr(
        "app.financial.router._agency_carryover_baseline", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr("app.financial.router.list_client_map", lambda **_k: [], raising=False)
    monkeypatch.setattr("app.financial.router.list_job_overrides", lambda **_k: [], raising=False)
    monkeypatch.setattr("app.financial.router.list_customers", lambda *_a, **_k: [], raising=False)
    monkeypatch.setattr(
        "app.financial.router.overview_from_cache",
        lambda: {"projects": [], "sync_status": "missing", "errors": {}},
        raising=False,
    )
    monkeypatch.setattr(
        "app.financial.router._safe_get_teamwork_insight", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router._safe_get_latest_insight", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router._safe_prior_payload", lambda *_a, **_k: None, raising=False
    )
    monkeypatch.setattr(
        "app.financial.router.list_capacity_snapshots", lambda *_a, **_k: [], raising=False
    )
    response = client.request(method, BASE + path, **kwargs)
    assert response.status_code != 404 or "Not Found" not in response.text[:80]


# ── Hard failures that currently 500 ─────────────────────────────────────────


@pytest.mark.parametrize("q", _FILTER_BREAKERS)
def test_client_map_search_never_500s_on_filter_metacharacters(q):
    """list_client_map interpolates `q` into PostgREST or_() — quotes/commas 500."""
    response = client.get(BASE + "/client-map", params={"q": q})
    assert response.status_code < 500, response.text[:300]


@pytest.mark.parametrize(
    "method,path",
    [
        ("PATCH", "/client-map/not-a-uuid"),
        ("DELETE", "/client-map/not-a-uuid"),
        ("DELETE", "/client-map/job-overrides/not-a-uuid"),
        ("PATCH", "/client-map/00000000-0000-0000-0000-000000000000garbage"),
    ],
)
def test_invalid_row_id_is_client_error_not_500(method, path):
    """Supabase UUID columns raise on garbage ids; router must map to 4xx (not 500)."""
    kwargs = {"json": {"client_name": "Z"}} if method == "PATCH" else {}
    response = client.request(method, BASE + path, **kwargs)
    assert response.status_code < 500, response.text[:300]
    assert response.status_code in {400, 404, 422}, (
        f"expected 4xx for bad id, got {response.status_code}: {response.text[:200]}"
    )


def test_delete_missing_client_map_reports_not_found(monkeypatch):
    monkeypatch.setattr("app.financial.router.delete_client_map", lambda _id: None)
    monkeypatch.setattr("app.financial.router.get_client_map_row", lambda _id: None, raising=False)
    missing = str(uuid.uuid4())
    response = client.delete(BASE + f"/client-map/{missing}")
    # Today returns {"deleted": true} even when nothing existed.
    assert response.status_code == 404, response.json()


# ── Validation / soft acceptance bugs ────────────────────────────────────────


def test_client_map_rejects_blank_tag_and_name(monkeypatch):
    insert = Mock(side_effect=AssertionError("blank row must not be inserted"))
    monkeypatch.setattr("app.financial.router.insert_client_map", insert)
    response = client.post(
        BASE + "/client-map",
        json={"tag_code": "", "client_name": ""},
    )
    assert response.status_code == 422
    insert.assert_not_called()


def test_checklist_status_rejects_arbitrary_strings():
    response = client.post(
        BASE + "/checklist/update",
        json={"id": 1, "status": "<script>alert(1)</script>"},
    )
    assert response.status_code == 422


def test_audit_resolve_rejects_unknown_id():
    response = client.post(
        BASE + "/audit-queue/resolve",
        json={"id": "ghost-does-not-exist", "action": "ignore"},
    )
    assert response.status_code == 404


def test_quickbooks_overview_year_has_bounds_like_agency():
    """Agency overview clamps year to 2000..2100; QB overview should too."""
    lo = client.get(BASE + "/quickbooks/overview", params={"year": 1999})
    hi = client.get(BASE + "/quickbooks/overview", params={"year": 9999})
    neg = client.get(BASE + "/quickbooks/overview", params={"year": -1})
    assert lo.status_code == 422
    assert hi.status_code == 422
    assert neg.status_code == 422


def test_agency_overview_year_bounds_enforced():
    assert client.get(BASE + "/agency/overview", params={"year": 1999}).status_code == 422
    assert client.get(BASE + "/agency/overview", params={"year": 2101}).status_code == 422


def test_chat_cost_requires_nonempty_thread_id():
    response = client.get(BASE + "/quickbooks/ai-insights/chat/cost", params={"thread_id": ""})
    assert response.status_code == 422


@pytest.mark.parametrize("source", ["quickbooks", "teamwork", "agency"])
def test_chat_rejects_empty_message(source, monkeypatch):
    # Stub LLM/overview so a regression that calls the model still fails on status.
    if source == "quickbooks":
        monkeypatch.setattr("app.financial.router._load_overview", lambda year: {"year": year})
        monkeypatch.setattr("app.financial.router._safe_prior_payload", lambda *a, **k: None)
    elif source == "teamwork":
        monkeypatch.setattr(
            "app.financial.router._teamwork_insight_inputs",
            lambda: ("site", {"sync_status": "ok", "errors": {}}, []),
        )
    else:
        monkeypatch.setattr("app.financial.router.build_agency_overview", lambda **k: {"jobs": []})
        monkeypatch.setattr("app.financial.router._agency_insight_row", lambda: None)
        monkeypatch.setattr("app.financial.router._agency_carryover_baseline", lambda *_a, **_k: None)

    response = client.post(BASE + f"/{source}/ai-insights/chat", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.parametrize("source", ["quickbooks", "teamwork", "agency"])
def test_chat_rejects_oversized_message_before_llm(source, monkeypatch):
    called = {"n": 0}

    async def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("oversized prompt must not reach the model")

    if source == "quickbooks":
        monkeypatch.setattr("app.financial.router._load_overview", lambda year: {"year": year})
        monkeypatch.setattr("app.financial.router._safe_prior_payload", lambda *a, **k: None)
        monkeypatch.setattr("app.financial.qb_chat.answer", boom)
    elif source == "teamwork":
        monkeypatch.setattr(
            "app.financial.router._teamwork_insight_inputs",
            lambda: ("site", {"sync_status": "ok", "errors": {}}, []),
        )
        monkeypatch.setattr("app.financial.teamwork.teamwork_chat.answer", boom)
    else:
        monkeypatch.setattr("app.financial.router.build_agency_overview", lambda **k: {"jobs": []})
        monkeypatch.setattr("app.financial.router._agency_insight_row", lambda: None)
        monkeypatch.setattr("app.financial.router._agency_carryover_baseline", lambda *_a, **_k: None)
        monkeypatch.setattr("app.financial.agency_chat.answer", boom)

    response = client.post(
        BASE + f"/{source}/ai-insights/chat",
        json={"message": "x" * 5000},
    )
    # Prefer hard reject; silent truncate that still bills is a soft fail.
    assert response.status_code == 422
    assert called["n"] == 0


# ── Auth edges ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/quickbooks/sync",
        "/teamwork/sync",
        "/agency/ai-insights/snapshot",
        "/agency/ai-insights/generate",
    ],
)
def test_cron_routes_reject_missing_secret(path, monkeypatch):
    monkeypatch.setattr("app.financial.router.settings.quickbooks_cron_secret", "real-secret")
    response = client.post(BASE + path, json={"mode": "auto"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/quickbooks/sync",
        "/teamwork/sync",
        "/agency/ai-insights/snapshot",
        "/agency/ai-insights/generate",
    ],
)
def test_cron_routes_reject_wrong_secret(path, monkeypatch):
    monkeypatch.setattr("app.financial.router.settings.quickbooks_cron_secret", "real-secret")
    response = client.post(
        BASE + path,
        json={"mode": "auto"},
        headers={"X-Cron-Secret": "wrong"},
    )
    assert response.status_code == 401


# ── Invoice resolution edges ─────────────────────────────────────────────────


def test_invoice_resolution_linked_requires_project_id():
    response = client.post(
        BASE + "/agency/invoice-resolutions",
        json={"invoice_id": "1", "resolution": "linked"},
    )
    assert response.status_code == 422


def test_invoice_resolution_internal_forbids_project_id():
    response = client.post(
        BASE + "/agency/invoice-resolutions",
        json={"invoice_id": "1", "resolution": "internal", "project_id": "99"},
    )
    assert response.status_code == 422


def test_invoice_resolution_unknown_invoice_404(monkeypatch):
    monkeypatch.setattr("app.financial.router.list_invoices", lambda *_a, **_k: [])
    response = client.post(
        BASE + "/agency/invoice-resolutions",
        json={"invoice_id": "missing", "resolution": "internal"},
    )
    assert response.status_code == 404


def test_job_override_unknown_client_map_404(monkeypatch):
    monkeypatch.setattr("app.financial.router.get_client_map_row", lambda _id: None)
    response = client.post(
        BASE + "/client-map/job-overrides",
        json={
            "site_id": "x",
            "project_id": 1,
            "client_map_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404


# ── Link endpoint type coercion ──────────────────────────────────────────────


def test_client_map_link_rejects_non_bool_include_ai(monkeypatch):
    monkeypatch.setattr(
        "app.financial.router.run_client_map_link",
        AsyncMock(side_effect=AssertionError("must not run")),
    )
    response = client.post(BASE + "/client-map/link", json={"include_ai": "yes"})
    assert response.status_code == 422
