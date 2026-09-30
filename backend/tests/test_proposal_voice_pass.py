import asyncio

from app.models.proposal import ProposalDraft, ProposalSection, VoiceFinding
from app.services import proposal_voice_llm as pv
from app.services import proposal_voice_pass as vp


def _section(id_, mode="write", content="Some text with enough words in it."):
    return ProposalSection(id=id_, title=id_.title(), mode=mode, content=content)


def _draft(sections, **kw):
    return ProposalDraft(rfpId="r1", sections=sections, updatedAt="2026-09-30T00:00:00Z", **kw)


def _stub(monkeypatch, *, rewrite=None, seen=None):
    monkeypatch.setattr(vp, "voice_standards_for", lambda rfp_id=None: ("STD", "r7"))

    async def fake(text, **kw):
        if seen is not None:
            seen.append({"text": text, **kw})
        if rewrite:
            return rewrite(text, kw)
        return pv.VoiceResult(text=text, reviewed=[f"h:{text[:8]}"])

    monkeypatch.setattr(pv, "rewrite_for_voice", fake)


def test_default_reviews_only_write_mode_sections(monkeypatch):
    seen = []
    _stub(monkeypatch, seen=seen)
    d = _draft([_section("a"), _section("b", mode="pull"), _section("c", mode="select"), _section("e", content="  ")])
    asyncio.run(vp.apply_voice_pass(d))
    assert [s["text"] for s in seen] == ["Some text with enough words in it."]


def test_scoped_call_reviews_named_sections_even_if_pulled(monkeypatch):
    seen = []
    _stub(monkeypatch, seen=seen)
    d = _draft([_section("a"), _section("b", mode="pull")])
    asyncio.run(vp.apply_voice_pass(d, section_ids={"b"}))
    assert len(seen) == 1


def test_standards_revision_and_reviewed_set_are_passed_through(monkeypatch):
    seen = []
    _stub(monkeypatch, seen=seen)
    d = _draft([_section("a")], voiceReviewed=["old"])
    asyncio.run(vp.apply_voice_pass(d))
    assert seen[0]["standards"] == "STD" and seen[0]["rev_id"] == "r7"
    assert "old" in seen[0]["reviewed"]


def test_new_hashes_are_merged_and_capped(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(vp, "MAX_REVIEWED", 3)
    d = _draft([_section("a")], voiceReviewed=["1", "2", "3"])
    out, _ = asyncio.run(vp.apply_voice_pass(d))
    assert out.voice_reviewed == ["2", "3", "h:Some tex"]


def test_edited_text_is_written_back_and_logged(monkeypatch):
    _stub(
        monkeypatch,
        rewrite=lambda text, kw: pv.VoiceResult(
            text=text.replace("enough", "plenty of"),
            applied=[pv.VoiceEdit("enough", "plenty of", "hedge", "hard")],
        ),
    )
    out, logs = asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    assert "plenty of" in out.sections[0].content
    assert any(line.startswith("a: ") for line in logs)


def test_only_verifier_rejections_and_suggestions_become_findings(monkeypatch):
    def rewrite(text, kw):
        return pv.VoiceResult(
            text=text,
            rejected=[
                (pv.VoiceEdit("Some text", "x", "instruction-leak", "hard"), "verifier: drops a promise"),
                (pv.VoiceEdit("with enough", "y", "hedge", "hard"), "find matched 2 times"),
            ],
            suggested=[pv.VoiceEdit("in it", "in this", "tense", "soft")],
        )

    _stub(monkeypatch, rewrite=rewrite)
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    kinds = {(f.find, f.kind) for f in out.voice_findings}
    assert kinds == {("Some text", "needs_human"), ("in it", "suggestion")}


def test_stale_findings_are_dropped_and_other_sections_kept(monkeypatch):
    _stub(monkeypatch)
    stale = VoiceFinding(sectionId="a", find="gone text", rule="r", kind="needs_human")
    keep = VoiceFinding(sectionId="b", find="Some text", rule="r", kind="needs_human")
    d = _draft([_section("a"), _section("b", mode="pull")], voiceFindings=[stale, keep])
    out, _ = asyncio.run(vp.apply_voice_pass(d))
    assert [f.section_id for f in out.voice_findings] == ["b"]
