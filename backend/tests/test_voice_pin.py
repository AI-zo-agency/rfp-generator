from types import SimpleNamespace

from app.models.proposal import ProposalDraft
from app.services import proposal_repository as repo


def _draft(pin=None):
    return ProposalDraft(rfpId="r1", sections=[], updatedAt="2026-09-30T00:00:00Z", voiceRevId=pin)


def _active(monkeypatch, rev_id="active1"):
    monkeypatch.setattr(
        "app.services.brand_voice_revisions.active_revision", lambda: SimpleNamespace(id=rev_id)
    )


def test_first_save_pins_the_active_revision(monkeypatch):
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: None)
    _active(monkeypatch)
    d = _draft()
    repo._stamp_voice_rev(d)
    assert d.voice_rev_id == "active1"


def test_autosave_keeps_the_stored_pin(monkeypatch):
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: _draft("stored"))
    _active(monkeypatch)
    d = _draft()  # the UI sends no pin
    repo._stamp_voice_rev(d)
    assert d.voice_rev_id == "stored"


def test_an_incoming_pin_is_never_overwritten(monkeypatch):
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: _draft("stored"))
    _active(monkeypatch)
    d = _draft("chosen")
    repo._stamp_voice_rev(d)
    assert d.voice_rev_id == "chosen"


def test_pin_failure_never_blocks_a_save(monkeypatch):
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: None)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.brand_voice_revisions.active_revision", boom)
    d = _draft()
    repo._stamp_voice_rev(d)
    assert d.voice_rev_id is None
