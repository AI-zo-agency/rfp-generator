from unittest.mock import patch

import pytest

from app.financial import qb_insights


def _overview():
    return {
        "errors": {},
        "sync_status": "ok",
        "ar": {
            "total": 14_419, "invoice_count": 3, "overdue_total": 14_419,
            "buckets": [{"label": "90+ days", "amount": 14_419}],
            "clients": [{"client": "City of Umatilla", "amount": 14_419,
                         "invoices": 3, "oldest_days": 95}],
        },
    }


def test_build_evidence_carries_signals_and_both_row_lists():
    evidence = qb_insights.build_evidence(_overview())
    assert {"position", "signals", "derived", "chase", "hygiene"} == set(evidence)
    assert evidence["chase"][0]["id"] == "chase:cityofumatilla"
    assert any(s["id"] == "ar-late" for s in evidence["signals"])


def test_build_messages_includes_every_row_id_the_model_may_annotate():
    evidence = qb_insights.build_evidence(_overview())
    messages = qb_insights.build_messages(evidence)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "chase:cityofumatilla" in messages[1]["content"]


def test_validate_keeps_notes_for_known_rows():
    evidence = qb_insights.build_evidence(_overview())
    out = qb_insights.validate_response(
        {"brief": "  Receivables are the problem.  ",
         "notes": {"chase:cityofumatilla": "  95 days out, largest balance.  "}},
        evidence,
    )
    assert out["brief"] == "Receivables are the problem."
    assert out["notes"] == {"chase:cityofumatilla": "95 days out, largest balance."}


def test_validate_drops_notes_with_bad_values_for_a_known_row():
    evidence = qb_insights.build_evidence(_overview())
    known_id = "chase:cityofumatilla"
    for bad_value in (42, "", "   "):
        out = qb_insights.validate_response(
            {"brief": "ok", "notes": {known_id: bad_value}}, evidence
        )
        assert out["notes"] == {}


def test_validate_keeps_a_valid_note_while_dropping_a_bad_sibling():
    overview = _overview()
    overview["ar"]["clients"].append(
        {"client": "Second Client", "amount": 5_000, "invoices": 1, "oldest_days": 10}
    )
    evidence = qb_insights.build_evidence(overview)
    ids = sorted(qb_insights.row_ids(evidence["chase"]))
    assert len(ids) == 2
    good_id, bad_id = ids
    out = qb_insights.validate_response(
        {"brief": "ok", "notes": {good_id: "worth a call today.", bad_id: 42}},
        evidence,
    )
    assert out["notes"] == {good_id: "worth a call today."}


def test_validate_drops_notes_for_rows_that_were_never_sent():
    evidence = qb_insights.build_evidence(_overview())
    out = qb_insights.validate_response(
        {"brief": "ok", "notes": {"chase:inventedclient": "call them"}}, evidence
    )
    assert out["notes"] == {}


def test_validate_tolerates_a_missing_or_malformed_notes_block():
    evidence = qb_insights.build_evidence(_overview())
    assert qb_insights.validate_response({"brief": "ok"}, evidence)["notes"] == {}
    assert qb_insights.validate_response(
        {"brief": "ok", "notes": "nope"}, evidence
    )["notes"] == {}


def test_validate_rejects_a_missing_brief():
    evidence = qb_insights.build_evidence(_overview())
    with pytest.raises(ValueError):
        qb_insights.validate_response({"notes": {}}, evidence)
    with pytest.raises(ValueError):
        qb_insights.validate_response({"brief": "   "}, evidence)


def test_generate_and_store_persists_ok_on_a_good_response():
    async def fake_chat(*args, **kwargs):
        return {"brief": "Umatilla is the whole problem.",
                "notes": {"chase:cityofumatilla": "95 days out."}}, "openrouter"

    with patch.object(qb_insights, "chat_json_soft", fake_chat), \
         patch.object(qb_insights, "upsert_insight") as upsert:
        status = qb_insights.generate_and_store("r1", _overview(), "2026-08-21")

    assert status == "ok"
    kwargs = upsert.call_args.kwargs
    assert kwargs["status"] == "ok"
    assert kwargs["scope_key"] == "r1"
    assert kwargs["as_of"] == "2026-08-21"
    assert kwargs["payload"]["brief"] == "Umatilla is the whole problem."
    assert kwargs["evidence"]["chase"][0]["id"] == "chase:cityofumatilla"


def test_generate_and_store_persists_failed_when_the_provider_gives_up():
    async def fake_chat(*args, **kwargs):
        return {}, "failed"

    with patch.object(qb_insights, "chat_json_soft", fake_chat), \
         patch.object(qb_insights, "upsert_insight") as upsert:
        status = qb_insights.generate_and_store("r1", _overview(), "2026-08-21")

    assert status == "failed"
    assert upsert.call_args.kwargs["status"] == "failed"


def test_generate_and_store_persists_failed_on_a_malformed_response():
    async def fake_chat(*args, **kwargs):
        return {"notes": {}}, "openrouter"

    with patch.object(qb_insights, "chat_json_soft", fake_chat), \
         patch.object(qb_insights, "upsert_insight") as upsert:
        status = qb_insights.generate_and_store("r1", _overview(), "2026-08-21")

    assert status == "failed"
    assert upsert.call_args.kwargs["status"] == "failed"


def test_generate_and_store_never_raises_when_persistence_itself_fails():
    async def fake_chat(*args, **kwargs):
        return {"brief": "fine", "notes": {}}, "openrouter"

    def boom(**kwargs):
        raise RuntimeError("supabase down")

    with patch.object(qb_insights, "chat_json_soft", fake_chat), \
         patch.object(qb_insights, "upsert_insight", boom):
        assert qb_insights.generate_and_store("r1", _overview(), "2026-08-21") == "failed"


def test_nightly_hook_swallows_every_failure():
    """generate_and_store is the sync's only contract: it returns, never raises."""
    async def explode(*args, **kwargs):
        raise RuntimeError("provider on fire")

    with patch.object(qb_insights, "chat_json_soft", explode), \
         patch.object(qb_insights, "upsert_insight"):
        assert qb_insights.generate_and_store("r1", {}, "2026-08-21") == "failed"


# ── figure guard ─────────────────────────────────────────────────────────────
# The evidence built from _overview() carries 14,419 / 95 / 3 and nothing else,
# so anything outside that set is unsupported.

def test_validate_drops_a_note_stating_a_figure_the_evidence_cannot_back():
    evidence = qb_insights.build_evidence(_overview())
    out = qb_insights.validate_response(
        {"brief": "ok",
         "notes": {"chase:cityofumatilla": "Nearly half a million is outstanding."}},
        evidence,
    )
    assert out["notes"] == {}


def test_validate_keeps_a_clean_note_while_dropping_one_with_a_bad_figure():
    overview = _overview()
    overview["ar"]["clients"].append(
        {"client": "Second Client", "amount": 5_000, "invoices": 1, "oldest_days": 10}
    )
    evidence = qb_insights.build_evidence(overview)
    good, bad = sorted(qb_insights.row_ids(evidence["chase"]))
    out = qb_insights.validate_response(
        {"brief": "ok",
         "notes": {good: "Ninety-five days out.", bad: "Nearly four times what we hold."}},
        evidence,
    )
    assert out["notes"] == {good: "Ninety-five days out."}


def test_validate_rejects_a_brief_stating_an_unsupported_figure():
    evidence = qb_insights.build_evidence(_overview())
    with pytest.raises(ValueError, match="unsupported quantity"):
        qb_insights.validate_response(
            {"brief": "Nearly three-quarters of a million sits unclassified.",
             "notes": {}},
            evidence,
        )


def test_validate_rejects_a_brief_claiming_magnitude_without_a_share():
    evidence = qb_insights.build_evidence(_overview())
    with pytest.raises(ValueError, match="unsupported quantity"):
        qb_insights.validate_response(
            {"brief": "Umatilla is the bulk of the aging book.", "notes": {}}, evidence
        )


def test_validate_accepts_a_brief_that_quotes_its_figures_correctly():
    evidence = qb_insights.build_evidence(_overview())
    out = qb_insights.validate_response(
        {"brief": "$14,419 is outstanding and the oldest invoice is 95 days out.",
         "notes": {}},
        evidence,
    )
    assert out["brief"].startswith("$14,419")


def test_a_guarded_brief_failure_is_stored_with_the_offending_text():
    async def fake_chat(*args, **kwargs):
        return {"brief": "Nearly three-quarters of a million is unclassified.",
                "notes": {}}, "openrouter"

    with patch.object(qb_insights, "chat_json_soft", fake_chat), \
         patch.object(qb_insights, "upsert_insight") as upsert:
        status = qb_insights.generate_and_store("r1", _overview(), "2026-08-21")

    assert status == "failed"
    kwargs = upsert.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert "three-quarters" in kwargs["error"]


def test_evidence_hides_the_internal_ranking_keys_from_the_model():
    """`dollar_days` licensed a wrong figure, so it must not reach the model.

    OCF's dollar_days is 11,966 x 72 = 861,552. With that in the evidence the
    guard accepted "nearly three-quarters of a million" as a description of
    $288,199, because 750,000 sits inside the verbal tolerance of 861,552.
    """
    evidence = qb_insights.build_evidence(_overview())
    for row in evidence["chase"] + evidence["hygiene"]:
        assert "dollar_days" not in row
        assert "amount" not in row
        assert "overdue_amount" not in row
    # The rows themselves keep the keys; only the model's copy is projected.
    assert "dollar_days" in qb_insights.chase_rows(_overview())[0]


def test_the_model_gets_the_average_age_and_never_the_oldest():
    """Handed `overdue_days` beside an amount, the model wrote "OCF is $11,966
    overdue at 73 days" — the implicature the per-invoice split exists to kill,
    reintroduced in prose. Only the age that pairs truthfully is sent."""
    evidence = qb_insights.build_evidence(_overview())
    row = evidence["chase"][0]
    assert "overdue_days" not in row
    assert "avg_overdue_days" in row
    # The UI still needs the oldest, so the row itself keeps it.
    assert "overdue_days" in qb_insights.chase_rows(_overview())[0]


def test_the_verbal_tolerance_cannot_be_widened_by_a_derived_internal_number():
    overview = _overview()
    overview["ar"]["clients"][0].update(amount=11_966, oldest_days=72)
    evidence = qb_insights.build_evidence(overview)
    with pytest.raises(ValueError, match="unsupported quantity"):
        qb_insights.validate_response(
            {"brief": "Nearly three-quarters of a million sits unclassified.",
             "notes": {}},
            evidence,
        )
