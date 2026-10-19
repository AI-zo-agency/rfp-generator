import asyncio

from app.models.proposal import ProposalDraft, ProposalSection, VoiceFinding
from app.services import proposal_voice_llm as pv
from app.services import proposal_voice_pass as vp
from app.services import proposal_voice_enforcement as ve


def _section(id_, mode="write", content="Some text with enough words in it."):
    return ProposalSection(id=id_, title=id_.title(), mode=mode, content=content)


def _draft(sections, **kw):
    return ProposalDraft(rfpId="r1", sections=sections, updatedAt="2026-09-30T00:00:00Z", **kw)


def _hashes(content: str) -> list[str]:
    return [pv.block_hash(b, "r7") for b in pv._blocks(content)]


def _stub(monkeypatch, *, check=None, repair=None, checks=None, repairs=None):
    monkeypatch.setattr(vp, "voice_standards_for", lambda rfp_id=None: ("STD", "r7"))

    async def fake_check(content, standards, *, rfp_id=None):
        if checks is not None:
            checks.append({"content": content, "standards": standards, "rfp_id": rfp_id})
        if check:
            return check(content)
        return {"misses": []}

    async def fake_repair(content, standards, quotes, *, rfp_id=None):
        if repairs is not None:
            repairs.append({"content": content, "quotes": quotes, "standards": standards})
        if repair:
            return repair(content, quotes)
        return {"edits": []}

    monkeypatch.setattr(vp, "check_section", fake_check)
    monkeypatch.setattr(vp, "repair_section", fake_repair)

    def boom(*_a, **_k):
        raise AssertionError("rewrite_for_voice must not run")

    monkeypatch.setattr(pv, "rewrite_for_voice", boom)


def test_default_reviews_only_write_mode_sections(monkeypatch):
    checks = []
    _stub(monkeypatch, checks=checks)
    d = _draft([_section("a"), _section("b", mode="pull"), _section("c", mode="select"), _section("e", content="  ")])
    asyncio.run(vp.apply_voice_pass(d))
    assert [c["content"] for c in checks] == ["Some text with enough words in it."]


def test_scoped_call_reviews_named_sections_even_if_pulled(monkeypatch):
    checks = []
    _stub(monkeypatch, checks=checks)
    d = _draft([_section("a"), _section("b", mode="pull")])
    asyncio.run(vp.apply_voice_pass(d, section_ids={"b"}))
    assert len(checks) == 1
    assert checks[0]["content"].startswith("Some text")


def test_standards_are_passed_to_the_check(monkeypatch):
    checks = []
    _stub(monkeypatch, checks=checks)
    asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    assert checks[0]["standards"] == "STD"


def test_invented_quote_is_dropped_and_does_not_repair(monkeypatch):
    repairs = []
    _stub(
        monkeypatch,
        repairs=repairs,
        check=lambda content: {"misses": [{"quote": "this sentence was invented", "rule": "negation"}]},
    )
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    assert repairs == []
    assert out.voice_findings == []
    assert out.voice_reviewed == _hashes("Some text with enough words in it.")


def test_grounded_quote_repairs_once_and_ignores_other_finds(monkeypatch):
    repairs = []
    body = "Honest cost is the heading. Repeat this line. Repeat this line."

    def check(content):
        if content == body:
            return {"misses": [
                {"quote": "Honest cost is the heading.", "rule": "editorial-heading"},
                {"quote": "not in the section", "rule": "negation"},
            ]}
        return {"misses": []}

    def repair(content, quotes):
        return {"edits": [
            {"find": "Honest cost is the heading.", "replace": "The cost is in the table."},
            {"find": "invented find", "replace": "nope"},
            {"find": "Repeat this line.", "replace": "Once."},
        ]}

    _stub(monkeypatch, repairs=repairs, check=check, repair=repair)
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a", content=body)])))
    assert len(repairs) == 1
    text = out.sections[0].content
    assert "The cost is in the table." in text
    assert "nope" not in text
    assert text.count("Repeat this line.") == 2
    assert not any(f.kind == "needs_human" for f in out.voice_findings)
    assert out.voice_reviewed == _hashes(text)


def test_second_check_miss_is_a_finding_and_not_reviewed(monkeypatch):
    body = "Honest cost is the heading."
    replacement = "The heading still comments."

    def check(content):
        if "Honest cost" in content:
            return {"misses": [{"quote": "Honest cost is the heading.", "rule": "editorial-heading"}]}
        return {"misses": [{"quote": replacement, "rule": "editorial-heading"}]}

    def repair(content, quotes):
        return {"edits": [{"find": "Honest cost is the heading.", "replace": replacement}]}

    _stub(monkeypatch, check=check, repair=repair)
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a", content=body)])))
    assert out.sections[0].content == replacement
    assert [(f.find, f.kind) for f in out.voice_findings] == [(replacement, "needs_human")]
    assert out.voice_reviewed == []


def test_clean_first_check_makes_no_repair_call(monkeypatch):
    repairs = []
    _stub(monkeypatch, repairs=repairs)
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    assert repairs == []
    assert out.voice_reviewed == _hashes("Some text with enough words in it.")


def test_apply_voice_pass_does_not_call_rewrite_for_voice(monkeypatch):
    _stub(monkeypatch)
    asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))


def test_em_dash_is_a_comma_before_the_check(monkeypatch):
    checks = []
    _stub(monkeypatch, checks=checks)
    d = _draft([_section("a", content="We'll ship it — ZO Agency leads.")])
    asyncio.run(ve.apply_rev6_voice_scrub_to_draft(d))
    assert "—" not in checks[0]["content"]
    assert "zö agency" in checks[0]["content"]


def test_stale_findings_are_dropped_and_other_sections_kept(monkeypatch):
    _stub(monkeypatch)
    stale = VoiceFinding(sectionId="a", find="gone text", rule="r", kind="needs_human")
    keep = VoiceFinding(sectionId="b", find="Some text", rule="r", kind="needs_human")
    d = _draft([_section("a"), _section("b", mode="pull")], voiceFindings=[stale, keep])
    out, _ = asyncio.run(vp.apply_voice_pass(d))
    assert [f.section_id for f in out.voice_findings] == ["b"]


def test_one_failing_section_does_not_sink_the_others(monkeypatch, caplog):
    def check(content):
        if "boom" in content:
            raise RuntimeError("bad block")
        return {"misses": []}

    _stub(monkeypatch, check=check)
    d = _draft([_section("a"), _section("b", content="boom goes the enough text")])
    with caplog.at_level("WARNING", logger=vp.logger.name):
        out, _logs = asyncio.run(vp.apply_voice_pass(d))
    assert out.sections[0].content == "Some text with enough words in it."
    assert out.sections[1].content == "boom goes the enough text"
    assert out.voice_reviewed == _hashes("Some text with enough words in it.")
    assert "b: voice gate failed" in caplog.text


def test_invalid_check_does_not_mark_reviewed_or_invent_a_finding(monkeypatch):
    _stub(monkeypatch, check=lambda content: None)
    out, _ = asyncio.run(vp.apply_voice_pass(_draft([_section("a")])))
    assert out.voice_reviewed == []
    assert out.voice_findings == []


def test_chat_wrapper_scopes_to_the_named_sections(monkeypatch):
    checks = []
    _stub(monkeypatch, checks=checks)
    d = _draft([_section("budget"), _section("bio", content="Bio text — with a dash and enough words.")])
    out, _ = asyncio.run(ve.apply_chat_rev6_voice_to_draft(d, section_ids={"budget"}))
    assert len(checks) == 1
    assert "—" in out.sections[1].content


def test_compulsory_section_pass_returns_repaired_section(monkeypatch):
    def check(content):
        if "BAD approach" in content:
            return {"misses": [{"quote": "BAD approach, see below.", "rule": "editorial-heading"}]}
        return {"misses": []}

    def repair(content, quotes):
        return {"edits": [{"find": "BAD approach, see below.", "replace": "GOOD approach, see below."}]}

    _stub(monkeypatch, check=check, repair=repair)
    sec = _section("a", content="BAD approach — see below.")
    out, _ = asyncio.run(ve.apply_compulsory_rev6_to_section(sec, rfp_id="r1"))
    assert out.content == "GOOD approach, see below."
