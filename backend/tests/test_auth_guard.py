import pytest
from fastapi.testclient import TestClient

from app.api import auth_guard
from app.core.config import settings
from app.main import app

pytestmark = pytest.mark.real_auth
client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_supabase(monkeypatch):
    auth_guard._verified.clear()
    calls = []

    def fake(token):
        calls.append(token)
        emails = {"good": "sonja@zo.agency", "e2m": "dev@e2msolutions.com"}
        if token not in emails:
            raise ValueError("invalid JWT")  # what supabase-py raises for a bad token
        return emails[token]

    monkeypatch.setattr(auth_guard, "_supabase_email", fake)
    monkeypatch.setattr(settings, "quickbooks_cron_secret", "cron-s3cret")
    return calls


def test_no_token_is_rejected():
    response = client.get("/api/v1/leads")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_bad_token_is_rejected():
    assert client.get("/api/v1/leads", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_health_stays_public():
    assert client.get("/api/v1/health").status_code == 200


def _reached_route(headers):
    # A bad mode is rejected by the route itself, so 422 proves sign-in passed.
    return client.post("/api/v1/leads/hubspot/sync", headers=headers, json={"mode": "bogus"}).status_code == 422


def test_valid_token_passes_and_is_cached(_fake_supabase):
    for _ in range(3):
        assert _reached_route({"Authorization": "Bearer good"})
    assert _fake_supabase == ["good"]


def test_any_account_in_the_project_is_accepted():
    """E2M staff accounts are not zo.agency; the guard must not lock them out."""
    assert _reached_route({"Authorization": "Bearer e2m"})


def test_cron_secret_still_works_without_a_user():
    assert _reached_route({"X-Cron-Secret": "cron-s3cret"})
    assert not _reached_route({"X-Cron-Secret": "wrong"})
