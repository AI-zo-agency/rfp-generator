import asyncio

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_voice_llm as pv
from app.services import proposal_voice_pass as vp
from app.services.proposal_section_editor import (
    apply_chat_preview_quality_guards,
    changed_section_ids,
)


def _section(id_, content="Some text with enough words in it."):
    return ProposalSection(id=id_, title=id_.title(), mode="write", content=content)


def _draft(*sections):
    return ProposalDraft(rfpId="r1", sections=list(sections), updatedAt="2026-09-30T00:00:00Z")


def test_changed_section_ids_only_changed_new_and_focus():
    prior = _draft(_section("a"), _section("b"), _section("c"))
    current = _draft(_section("a", "edited words here"), _section("b"), _section("c"), _section("d"))
    assert changed_section_ids(prior, current) == {"a", "d"}
    assert changed_section_ids(prior, current, "b") == {"a", "b", "d"}


def test_changed_section_ids_without_prior_is_just_focus():
    current = _draft(_section("a"), _section("b"))
    assert changed_section_ids(None, current, "b") == {"b"}
    assert changed_section_ids(None, current) == set()


def _record_model_calls(monkeypatch):
    seen = []
    monkeypatch.setattr(vp, "voice_standards_for", lambda rfp_id=None: ("STD", "r7"))

    async def fake(text, **kw):
        seen.append(text)
        return pv.VoiceResult(text=text)

    monkeypatch.setattr(pv, "rewrite_for_voice", fake)
    return seen


def test_preview_guard_empty_scope_makes_no_model_call(monkeypatch):
    seen = _record_model_calls(monkeypatch)
    d = _draft(_section("a", "Alpha text with enough words."), _section("b", "Beta text with enough words."))
    asyncio.run(apply_chat_preview_quality_guards(d, section_ids=set()))
    assert seen == []


def test_preview_guard_named_scope_reviews_only_that_section(monkeypatch):
    seen = _record_model_calls(monkeypatch)
    d = _draft(_section("a", "Alpha text with enough words."), _section("b", "Beta text with enough words."))
    asyncio.run(apply_chat_preview_quality_guards(d, section_ids={"a"}))
    assert seen == ["Alpha text with enough words."]


def _stub_consistency_edits_b(monkeypatch):
    """Consistency stub: always rewrites sibling tab "b" (the cross-tab reach)."""
    from app.services import proposal_consistency_enforcement as ce

    def fake(draft):
        secs = [
            s.model_copy(update={"content": "CONSISTENT b"}) if s.id == "b" else s
            for s in draft.sections
        ]
        return draft.model_copy(update={"sections": secs}), ["b: aligned"]

    monkeypatch.setattr(ce, "apply_consistency_enforcement", fake)


def _content(draft, sid):
    return next(s.content for s in draft.sections if s.id == sid)


def test_unpinned_consistency_stays_draft_wide_while_voice_scope_is_separate(monkeypatch):
    seen = _record_model_calls(monkeypatch)
    _stub_consistency_edits_b(monkeypatch)
    d = _draft(_section("a", "Alpha text with enough words."), _section("b", "Beta text with enough words."))
    out = asyncio.run(apply_chat_preview_quality_guards(d, section_ids=None, voice_section_ids=set()))
    assert _content(out, "b") == "CONSISTENT b"
    assert seen == []


def test_pinned_consistency_still_restores_sibling_tabs(monkeypatch):
    _record_model_calls(monkeypatch)
    _stub_consistency_edits_b(monkeypatch)
    d = _draft(_section("a", "Alpha text with enough words."), _section("b", "Beta text with enough words."))
    out = asyncio.run(apply_chat_preview_quality_guards(d, section_ids={"a"}, voice_section_ids={"a"}))
    assert _content(out, "b") == "Beta text with enough words."


def test_voice_follows_section_ids_when_voice_scope_omitted(monkeypatch):
    seen = _record_model_calls(monkeypatch)
    d = _draft(_section("a", "Alpha text with enough words."), _section("b", "Beta text with enough words."))
    asyncio.run(apply_chat_preview_quality_guards(d, section_ids={"b"}))
    assert seen == ["Beta text with enough words."]
