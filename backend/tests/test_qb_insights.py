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
    assert {"signals", "chase", "hygiene"} == set(evidence)
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
