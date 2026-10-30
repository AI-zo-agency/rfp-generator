"""The activity actor is the verified token email; the client header is only a fallback."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.llm_call_context import get_llm_user_email


def _client(monkeypatch, verified: str | None) -> TestClient:
    from app.main import attach_llm_user_email

    monkeypatch.setattr("app.api.auth_guard.verified_email", lambda _token: verified)
    app = FastAPI()
    app.middleware("http")(attach_llm_user_email)

    @app.get("/who")
    def who() -> dict[str, str]:
        return {"email": get_llm_user_email()}

    return TestClient(app)


def test_verified_email_overrides_spoofed_header(monkeypatch):
    client = _client(monkeypatch, "real@zo.agency")
    res = client.get(
        "/who",
        headers={"Authorization": "Bearer t", "X-User-Email": "fake@x.com"},
    )
    assert res.json() == {"email": "real@zo.agency"}


def test_header_used_without_bearer(monkeypatch):
    client = _client(monkeypatch, "real@zo.agency")
    res = client.get("/who", headers={"X-User-Email": "a@zo.agency"})
    assert res.json() == {"email": "a@zo.agency"}


def test_header_used_when_token_invalid(monkeypatch):
    client = _client(monkeypatch, None)
    res = client.get(
        "/who",
        headers={"Authorization": "Bearer bad", "X-User-Email": "b@zo.agency"},
    )
    assert res.json() == {"email": "b@zo.agency"}


def test_no_identity_is_empty(monkeypatch):
    client = _client(monkeypatch, None)
    assert client.get("/who").json() == {"email": ""}
