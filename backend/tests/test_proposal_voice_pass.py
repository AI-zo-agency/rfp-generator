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


def test_one_failing_section_does_not_sink_the_others(monkeypatch, caplog):
    def rewrite(text, kw):
        if "boom" in text:
            raise RuntimeError("bad block")
        return pv.VoiceResult(text=text.replace("enough", "plenty of"), reviewed=["ok-hash"])

    _stub(monkeypatch, rewrite=rewrite)
    d = _draft([_section("a"), _section("b", content="boom goes the enough text")])
    with caplog.at_level("WARNING", logger=vp.logger.name):
        out, logs = asyncio.run(vp.apply_voice_pass(d))
    assert "plenty of" in out.sections[0].content
    assert out.sections[1].content == "boom goes the enough text"
    assert out.voice_reviewed == ["ok-hash"]
    # the gap is logged, not returned: callers count returned lines as fixes
    assert not any("incomplete" in line for line in logs)
    assert "b: voice pass incomplete (error: bad block" in caplog.text


def test_verifier_no_verdict_is_not_a_finding(monkeypatch):
    def rewrite(text, kw):
        return pv.VoiceResult(
            text=text,
            rejected=[
                (pv.VoiceEdit("Some text", "x", "hedge", "hard"), "verifier: no verdict"),
                (pv.VoiceEdit("in it", "y", "hedge", "hard"), "verifier: drops a promise"),
            ],
        )

    _stub(monkeypatch, rewrite=rewrite)
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    assert [f.find for f in out.voice_findings] == ["in it"]


from app.services import proposal_voice_enforcement as ve


def test_draft_scrub_keeps_mechanics_and_runs_the_pass(monkeypatch):
    _stub(
        monkeypatch,
        rewrite=lambda text, kw: pv.VoiceResult(text=text.replace("BAD", "GOOD")),
    )
    d = _draft([_section("a", content="BAD work — ZO Agency ships it.")])
    out, _ = asyncio.run(ve.apply_rev6_voice_scrub_to_draft(d))
    assert out.sections[0].content == "GOOD work, zö agency ships it."


def test_chat_wrapper_scopes_to_the_named_sections(monkeypatch):
    seen = []
    _stub(monkeypatch, seen=seen)
    d = _draft([_section("budget"), _section("bio", content="Bio text — with a dash and enough words.")])
    out, _ = asyncio.run(ve.apply_chat_rev6_voice_to_draft(d, section_ids={"budget"}))
    assert len(seen) == 1
    assert "—" in out.sections[1].content  # untouched tab stays untouched


def test_compulsory_section_pass_returns_rewritten_section(monkeypatch):
    _stub(monkeypatch, rewrite=lambda text, kw: pv.VoiceResult(text=text.replace("BAD", "GOOD")))
    sec = _section("a", content="BAD approach — see below.")
    out, _ = asyncio.run(ve.apply_compulsory_rev6_to_section(sec, rfp_id="r1"))
    assert out.content == "GOOD approach, see below."
