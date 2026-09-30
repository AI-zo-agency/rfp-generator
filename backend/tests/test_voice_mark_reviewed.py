"""mark_sections_reviewed: text a human approved is never sent to the model again."""

import asyncio

import pytest

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.services import llm, proposal_voice_llm as pv, proposal_voice_pass as vp

pytestmark = pytest.mark.real_voice_llm

A = "First paragraph of section a has enough words.\n\nSecond paragraph of section a has enough words."
B = "Only paragraph of section b has enough words."


@pytest.fixture(autouse=True)
def _rev(monkeypatch):
    monkeypatch.setattr(vp, "voice_standards_for", lambda rfp_id=None: ("STD", "r7"))


def _draft(**kw):
    sections = [
        ProposalSection(id="a", title="A", mode="write", content=A),
        ProposalSection(id="b", title="B", mode="write", content=B),
    ]
    return ProposalDraft(rfpId="r1", sections=sections, updatedAt="2026-09-30T00:00:00Z", **kw)


def _h(text):
    return pv.block_hash(text, "r7")


def test_adds_hashes_for_named_sections_only():
    out = vp.mark_sections_reviewed(_draft(), ["a"])
    assert out.voice_reviewed == [_h(b) for b in pv._blocks(A)]


def test_keeps_existing_hashes_and_dedupes():
    d = _draft(voiceReviewed=["old", _h(pv._blocks(A)[0])])
    out = vp.mark_sections_reviewed(d, ["a", "b"])
    assert out.voice_reviewed == ["old", _h(pv._blocks(A)[0]), _h(pv._blocks(A)[1]), _h(B)]
    assert d.voice_reviewed == ["old", _h(pv._blocks(A)[0])]  # input untouched


def test_respects_the_cap(monkeypatch):
    monkeypatch.setattr(vp, "MAX_REVIEWED", 2)
    out = vp.mark_sections_reviewed(_draft(voiceReviewed=["old"]), ["a", "b"])
    assert out.voice_reviewed == [_h(pv._blocks(A)[1]), _h(B)]


def test_marked_paragraph_is_skipped_by_the_pass(monkeypatch):
    calls = []

    async def fake(messages, **k):
        calls.append(messages)
        return {"edits": []}, "stub"

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    draft = vp.mark_sections_reviewed(_draft(), ["a"])
    res = asyncio.run(pv.rewrite_for_voice(A, rev_id="r7", reviewed=draft.voice_reviewed))
    assert calls == [] and res.text == A
    asyncio.run(pv.rewrite_for_voice(B, rev_id="r7", reviewed=draft.voice_reviewed))
    assert len(calls) == 1  # section b was not marked


def test_confirm_preview_marks_changed_sections_and_keeps_stored_hashes(monkeypatch):
    from app.api.v1 import proposals as api
    from app.services import proposal_repository as repo
    from app.services import proposal_zero_fabrication as zf

    edited = "Only paragraph of section b was edited by the person."
    prior = _draft(voiceReviewed=["stored-hash"])
    incoming = _draft()
    incoming.sections[1] = incoming.sections[1].model_copy(update={"content": edited})
    saved = []

    async def get(rfp_id):
        return prior

    async def guards(draft, **k):
        return draft, None

    async def save(draft):
        saved.append(draft)

    async def no_research(rfp_id):
        return ProposalResearchCache(rfpId=rfp_id, updatedAt="2026-09-30T00:00:00Z")

    monkeypatch.setattr(repo, "aget_proposal_draft", get)
    monkeypatch.setattr(repo, "aget_research_cache", no_research)
    monkeypatch.setattr(repo, "asave_proposal_draft", save)
    monkeypatch.setattr(zf, "apply_zero_fabrication_guards_before_persist", guards)

    asyncio.run(api.confirm_chat_preview_endpoint("r1", incoming))
    reviewed = saved[0].voice_reviewed
    assert reviewed == ["stored-hash", _h(edited)]  # stored state kept, only the changed tab marked
