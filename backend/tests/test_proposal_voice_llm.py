"""Edit applier and guards for proposal_voice_llm, with the model stubbed."""

import asyncio

import pytest

from app.services import llm, proposal_voice_llm as pv

pytestmark = pytest.mark.real_voice_llm

SECTION = (
    "This is a process commitment we're prepared to demonstrate, overstating.\n"
    "We haven't held a HIPAA-covered engagement. Bid that record accurately. Rates start at $275.\n"
    "[VERIFY: phone number]\n"
    "| Role | Rate |\n| --- | --- |\n| Director — lead | $400 |\n"
)


def run(edits, monkeypatch, *, apply=True, text=SECTION, error=None, verdict=True):
    async def fake_chat_json(messages, **k):
        if error:
            raise error
        if messages[0]["content"].startswith("You audit"):
            return {"verdicts": [{"i": i, "faithful": verdict, "reason": "stub"} for i in range(50)]}, "stub"
        block = messages[-1]["content"]
        return {"edits": [e for e in edits if e["find"] in block]}, "stub-model"

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    return asyncio.run(pv.rewrite_for_voice(text, apply=apply))


def edit(find, replace, rule="x", severity="hard"):
    return {"find": find, "replace": replace, "rule": rule, "severity": severity}


def test_applies_unique_edit_and_leaves_rest_identical(monkeypatch):
    fix = edit(
        "This is a process commitment we're prepared to demonstrate, overstating.",
        "We're prepared to demonstrate this process commitment.",
    )
    res = run([fix], monkeypatch)
    assert len(res.applied) == 1 and not res.rejected
    assert res.text == SECTION.replace(fix["find"], fix["replace"])


def test_deletion_takes_one_space(monkeypatch):
    res = run([edit("Bid that record accurately.", "")], monkeypatch)
    assert "accurately" not in res.text
    assert "engagement. Rates start" in res.text


def test_table_cell_edit(monkeypatch):
    res = run([edit("Director — lead", "Director: lead")], monkeypatch)
    assert "| Director: lead | $400 |" in res.text


def test_rejects_missing_or_ambiguous_find(monkeypatch):
    res = run([edit("not in the text", "x"), edit("$", "x")], monkeypatch)
    assert not res.applied and res.text == SECTION


def test_rejects_new_number_and_new_name(monkeypatch):
    res = run(
        [
            edit("Rates start at $275.", "Rates start at $300."),
            edit("Bid that record accurately.", "Ask Maricopa County for a reference."),
        ],
        monkeypatch,
    )
    assert not res.applied and res.text == SECTION


def test_rejects_tag_edit(monkeypatch):
    res = run([edit("[VERIFY: phone number]", "555-0100")], monkeypatch)
    assert not res.applied and res.text == SECTION


def test_soft_edit_is_suggested_not_applied(monkeypatch):
    res = run([edit("Bid that record accurately.", "", severity="soft")], monkeypatch)
    assert not res.applied and len(res.suggested) == 1 and res.text == SECTION


def test_edit_outside_reviewed_block_is_rejected(monkeypatch):
    async def wrong_block(messages, **k):
        if messages[0]["content"].startswith("You audit"):
            return {"verdicts": [{"i": i, "faithful": True} for i in range(50)]}, "stub"
        return {"edits": [edit("Rates start at $275.", "Rates start at $275 an hour.")]}, "stub"

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", wrong_block)
    res = asyncio.run(pv.rewrite_for_voice(SECTION))
    # the edit is returned for every block; only the block holding the sentence may apply it
    assert res.text.count("an hour") == 1


def test_verifier_rejection_blocks_edit(monkeypatch):
    res = run([edit("Bid that record accurately.", "")], monkeypatch, verdict=False)
    assert not res.applied and res.text == SECTION
    assert res.rejected and res.rejected[0][1].startswith("verifier")


def test_apply_false_reports_without_changing(monkeypatch):
    res = run([edit("Bid that record accurately.", "")], monkeypatch, apply=False)
    assert len(res.applied) == 1 and res.text == SECTION


def test_llm_error_returns_text_unchanged(monkeypatch):
    res = run([], monkeypatch, error=RuntimeError("budget"))
    assert res.text == SECTION and "llm error" in res.skipped


def test_garbage_model_output_is_ignored(monkeypatch):
    async def bad(*a, **k):
        return {"edits": "nope"}, "stub"

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", bad)
    res = asyncio.run(pv.rewrite_for_voice(SECTION))
    assert res.text == SECTION and not res.applied


TWO = "First paragraph has enough words to count.\n\nSecond paragraph also has enough words."


def _counting_stub(monkeypatch, edits=None, fail=False):
    calls = []

    async def fake(messages, **k):
        if messages[0]["content"].startswith("You audit"):
            return {"verdicts": [{"i": i, "faithful": True} for i in range(50)]}, "stub"
        if fail:
            raise RuntimeError("down")
        block = messages[-1]["content"]
        calls.append(block)
        return {"edits": [e for e in (edits or []) if e["find"] in block]}, "stub"

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake)
    return calls


def test_reviewed_blocks_are_skipped_and_reported(monkeypatch):
    calls = _counting_stub(monkeypatch)
    res = asyncio.run(pv.rewrite_for_voice(TWO, rev_id="r7"))
    assert len(calls) == 2 and len(res.reviewed) == 2

    asyncio.run(pv.rewrite_for_voice(TWO, rev_id="r7", reviewed=res.reviewed))
    assert len(calls) == 2  # nothing new to review

    asyncio.run(pv.rewrite_for_voice(TWO, rev_id="r8", reviewed=res.reviewed))
    assert len(calls) == 4  # a new revision makes every paragraph unreviewed


def test_edited_block_is_recorded_by_its_final_text(monkeypatch):
    fix = edit("Second paragraph also has enough words.", "Second paragraph has enough words.")
    _counting_stub(monkeypatch, [fix])
    res = asyncio.run(pv.rewrite_for_voice(TWO, rev_id="r7"))
    assert res.text.endswith("Second paragraph has enough words.")
    assert pv.block_hash("Second paragraph has enough words.", "r7") in res.reviewed


def test_failed_block_is_not_recorded(monkeypatch):
    _counting_stub(monkeypatch, fail=True)
    res = asyncio.run(pv.rewrite_for_voice(TWO, rev_id="r7"))
    assert res.reviewed == [] and "failed" in res.skipped


def test_apply_false_records_nothing(monkeypatch):
    _counting_stub(monkeypatch)
    res = asyncio.run(pv.rewrite_for_voice(TWO, rev_id="r7", apply=False))
    assert res.reviewed == []
