from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.financial import router as fin_router
from app.main import app

client = TestClient(app)


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
