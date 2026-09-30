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
