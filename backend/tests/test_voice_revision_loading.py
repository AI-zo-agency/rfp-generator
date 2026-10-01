from types import SimpleNamespace

import pytest

from app.services import brand_voice_revisions as bvr
from app.services import proposal_brand_voice as pbv
from app.services import proposal_repository as repo
from tests.fake_supabase import FakeDb

pytestmark = pytest.mark.real_revisions

MD6 = "# T\n\nrev 6 body\n\n## 1. Company name\nAlways zö.\n\n## 2. Writing rules\nSix rules.\n"
MD7 = MD6.replace("rev 6", "rev 7").replace("Six rules.", "Seven rules.\n\n## 5. Typography\nAnton 22pt.\n")


@pytest.fixture(autouse=True)
def db(monkeypatch):
    bvr._reset_caches()
    pbv._standards_for_revision.cache_clear()
    fake = FakeDb()
    monkeypatch.setattr(bvr, "enabled", lambda: True)
    monkeypatch.setattr(bvr, "_db", lambda: fake)
    yield fake
    bvr._reset_caches()
    pbv._standards_for_revision.cache_clear()


def _seed():
    a = bvr.add_revision(label="rev 6", body=MD6, created_by="t")
    b = bvr.add_revision(label="rev 7", body=MD7, created_by="t")
    bvr.set_active(a.id, updated_by="t")
    return a, b


def test_load_defaults_to_the_active_revision_and_labels_it():
    a, b = _seed()
    text = pbv.load_writing_standards()
    assert "(rev 6)" in text and "Six rules." in text and "COMPULSORY" in text
    bvr.set_active(b.id, updated_by="t")
    assert "(rev 7)" in pbv.load_writing_standards()


def test_load_by_id_sends_only_the_writing_rules():
    _, b = _seed()
    text = pbv.load_writing_standards(b.id)
    assert "Seven rules." in text and "Anton 22pt" not in text


def test_voice_standards_for_uses_the_proposals_pin(monkeypatch):
    a, b = _seed()  # active is rev 6
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: SimpleNamespace(voice_rev_id=b.id))
    text, rev_id = pbv.voice_standards_for("r1")
    assert rev_id == b.id and "(rev 7)" in text


def test_voice_standards_for_an_unpinned_proposal_uses_the_active_revision(monkeypatch):
    a, _ = _seed()
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: SimpleNamespace(voice_rev_id=None))
    assert pbv.voice_standards_for("r1")[1] == a.id
    assert pbv.voice_standards_for(None)[1] == a.id


def test_a_pin_to_a_missing_revision_falls_back_to_active(monkeypatch):
    a, _ = _seed()
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: SimpleNamespace(voice_rev_id="gone"))
    assert pbv.voice_standards_for("r1")[1] == a.id


def test_without_supabase_the_repo_file_governs_and_no_draft_is_read(monkeypatch):
    monkeypatch.setattr(bvr, "enabled", lambda: False)

    def boom(rfp_id):
        raise AssertionError("must not read a draft when only the repo file exists")

    monkeypatch.setattr(repo, "get_proposal_draft", boom)
    text, rev_id = pbv.voice_standards_for("r1")
    assert rev_id == bvr.BUILTIN_ID and "(rev 6)" in text


def test_full_voice_block_uses_the_pinned_revision_and_compact_does_not(monkeypatch):
    _, b = _seed()
    monkeypatch.setattr(repo, "get_proposal_draft", lambda rfp_id: SimpleNamespace(voice_rev_id=b.id))
    assert "(rev 7)" in pbv.format_brand_voice_block(None, rfp_id="r1")
    assert "(rev 7)" not in pbv.format_brand_voice_block(None, rfp_id="r1", compact=True)
