from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.models.proposal import ProposalDraft

client = TestClient(app)
URL = "/api/v1/rfps/r1/proposal/voice-rev"


def _draft(pin=None):
    return ProposalDraft(rfpId="r1", sections=[], updatedAt="2026-09-30T00:00:00Z", voiceRevId=pin)


def _revs(monkeypatch):
    revs = {"id1": SimpleNamespace(id="id1", label="rev 6"), "id2": SimpleNamespace(id="id2", label="rev 7")}
    monkeypatch.setattr("app.services.brand_voice_revisions.get_revision", lambda i: revs.get(i))
    monkeypatch.setattr("app.services.brand_voice_revisions.active_revision", lambda: revs["id1"])


def test_get_reports_pinned_and_active(monkeypatch):
    _revs(monkeypatch)

    async def fake_get(rfp_id):
        return _draft("id2")

    monkeypatch.setattr("app.services.proposal_repository.aget_proposal_draft", fake_get)
    body = client.get(URL).json()
    assert body == {"pinned": {"id": "id2", "label": "rev 7"}, "active": {"id": "id1", "label": "rev 6"}}


def test_get_with_no_pin_reports_null(monkeypatch):
    _revs(monkeypatch)

    async def fake_get(rfp_id):
        return _draft(None)

    monkeypatch.setattr("app.services.proposal_repository.aget_proposal_draft", fake_get)
    assert client.get(URL).json()["pinned"] is None


def test_put_saves_the_new_pin(monkeypatch):
    _revs(monkeypatch)
    saved = []

    async def fake_get(rfp_id):
        return _draft("id1")

    async def fake_save(draft):
        saved.append(draft)

    monkeypatch.setattr("app.services.proposal_repository.aget_proposal_draft", fake_get)
    monkeypatch.setattr("app.services.proposal_repository.asave_proposal_draft", fake_save)
    r = client.put(URL, json={"revisionId": "id2"})
    assert r.status_code == 200 and r.json()["pinned"]["id"] == "id2"
    assert saved[0].voice_rev_id == "id2"


def test_put_unknown_revision_is_404(monkeypatch):
    _revs(monkeypatch)

    async def fake_get(rfp_id):
        return _draft("id1")

    monkeypatch.setattr("app.services.proposal_repository.aget_proposal_draft", fake_get)
    assert client.put(URL, json={"revisionId": "nope"}).status_code == 404


def test_put_without_a_draft_is_404(monkeypatch):
    _revs(monkeypatch)

    async def fake_get(rfp_id):
        return None

    monkeypatch.setattr("app.services.proposal_repository.aget_proposal_draft", fake_get)
    assert client.put(URL, json={"revisionId": "id1"}).status_code == 404
