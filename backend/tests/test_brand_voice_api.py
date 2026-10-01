import pytest
from fastapi.testclient import TestClient

from app.api import auth_guard
from app.api.v1 import brand_voice
from app.main import app
from app.services import brand_voice_revisions as bvr
from tests.fake_supabase import FakeDb

pytestmark = [pytest.mark.real_auth, pytest.mark.real_revisions]

client = TestClient(app)
MD = "# T\n\n## 1. Company name\nAlways zö.\n\n## 2. Writing rules\nWrite plainly.\n"
AUTH = {"Authorization": "Bearer user"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    auth_guard._verified.clear()

    def fake(token):
        if token != "user":
            raise ValueError("invalid JWT")
        return "sonja@zo.agency"

    monkeypatch.setattr(auth_guard, "_supabase_email", fake)
    monkeypatch.setattr(brand_voice, "emit_activity", lambda **kw: None)
    bvr._reset_caches()
    fake_db = FakeDb()
    monkeypatch.setattr(bvr, "enabled", lambda: True)
    monkeypatch.setattr(bvr, "_db", lambda: fake_db)
    yield
    bvr._reset_caches()


def _upload(body=MD, label="rev 7", name="rev7.md"):
    return client.post(
        "/api/v1/brand-voice/revisions",
        headers=AUTH,
        files={"file": (name, body.encode("utf-8"), "text/markdown")},
        data={"label": label, "notes": "from the client"},
    )


def test_every_route_needs_sign_in():
    assert client.get("/api/v1/brand-voice/revisions").status_code == 401
    assert client.put("/api/v1/brand-voice/active", json={"revisionId": "x"}).status_code == 401


def test_listing_reports_that_editing_is_available():
    body = client.get("/api/v1/brand-voice/revisions", headers=AUTH).json()
    assert body["enabled"] is True and body["revisions"] == []


def test_a_signed_in_user_adds_a_revision_and_it_appears():
    r = _upload()
    assert r.status_code == 201 and r.json()["label"] == "rev 7" and r.json()["createdBy"] == "sonja@zo.agency"
    listed = client.get("/api/v1/brand-voice/revisions", headers=AUTH).json()["revisions"]
    assert [x["label"] for x in listed] == ["rev 7"]


def test_duplicate_upload_is_409_with_the_existing_id():
    first = _upload().json()
    r = _upload()
    assert r.status_code == 409 and r.json()["detail"]["existingId"] == first["id"]


@pytest.mark.parametrize("body,label", [("plain text", "rev 8"), (MD, ""), (MD, "x" * 41)])
def test_invalid_uploads_are_422(body, label):
    assert _upload(body=body, label=label).status_code == 422


def test_non_utf8_upload_is_422():
    r = client.post(
        "/api/v1/brand-voice/revisions",
        headers=AUTH,
        files={"file": ("x.md", b"\xff\xfe\x00bad", "text/markdown")},
        data={"label": "rev 9"},
    )
    assert r.status_code == 422


def test_set_active_then_list_marks_it():
    rid = _upload().json()["id"]
    r = client.put("/api/v1/brand-voice/active", headers=AUTH, json={"revisionId": rid})
    assert r.status_code == 200 and r.json()["isActive"] is True
    body = client.get("/api/v1/brand-voice/revisions", headers=AUTH).json()
    assert body["activeId"] == rid and body["revisions"][0]["isActive"] is True


def test_set_active_unknown_is_404():
    assert client.put("/api/v1/brand-voice/active", headers=AUTH, json={"revisionId": "nope"}).status_code == 404


def test_view_returns_the_body():
    rid = _upload().json()["id"]
    body = client.get(f"/api/v1/brand-voice/revisions/{rid}", headers=AUTH).json()
    assert body["body"] == MD
    assert client.get("/api/v1/brand-voice/revisions/nope", headers=AUTH).status_code == 404


def test_without_supabase_edits_answer_503(monkeypatch):
    monkeypatch.setattr(bvr, "enabled", lambda: False)
    assert _upload().status_code == 503
    assert client.put("/api/v1/brand-voice/active", headers=AUTH, json={"revisionId": "x"}).status_code == 503
    body = client.get("/api/v1/brand-voice/revisions", headers=AUTH).json()
    assert body["enabled"] is False and body["revisions"][0]["id"] == "builtin"
