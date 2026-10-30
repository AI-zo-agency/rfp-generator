"""The ledger: per-RFP runs, tweaks, opens and deletes, built from activity + tweak rows."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app  # imported here so conftest's sign-in bypass applies
from app.services import proposal_tweaks_store as store
from app.services import rfp_ledger as ledger
from app.services.user_activity import emit_activity

A, B, C = "a@zo.agency", "b@zo.agency", "c@zo.agency"


@pytest.fixture()
def db(sqlite_activity_store):
    ledger._cost_cache.clear()
    return sqlite_activity_store


def _row(action, actor, *, rfp="r1", title="Water Plant", run_id=None, outcome="recorded", meta=None):
    emit_activity(
        workspace="rfp",
        action=action,
        summary=f"{action} {title}",
        actor_email=actor,
        entity_type="rfp",
        entity_id=rfp,
        entity_label=title,
        metadata=meta or {},
        run_id=run_id,
        outcome=outcome,
    )


def _tweak(actor, *, source="manual_edit", bucket="manual", section="s1", added=0, removed=0,
           rfp="r1", instruction="", kind="text"):
    return store.insert_tweak(
        {
            "rfp_id": rfp, "rfp_title": "Water Plant", "section_id": section,
            "section_title": f"Section {section}", "actor_email": actor, "source": source,
            "bucket": bucket, "kind": kind, "instruction": instruction,
            "words_before": 10, "words_after": 10 + added - removed,
            "words_added": added, "words_removed": removed,
            "before_text": "SECRET OLD TEXT", "after_text": "SECRET NEW TEXT",
        }
    )


def _only(result):
    assert len(result["items"]) == 1
    return result["items"][0]


def test_ledger_row_counts_runs_tweaks_and_opens(db):
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-2"})
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-3", "chained": True})
    _row("proposal.phase_completed", A, run_id="L1", outcome="completed", meta={"phase": "phase-3", "llm_run_id": "l1"})
    _tweak(A, added=5, removed=2)
    _tweak(A, section="s2", added=3)
    _tweak(B, source="chat_apply", bucket="ai", removed=1)
    for who in (A, B, B):
        _row("rfp.opened", who)
    row = _only(ledger.list_rfp_ledger())
    assert row["rfp_id"] == "r1" and row["title"] == "Water Plant"
    assert row["ran_by"] == [A]
    assert row["run_count"] == 1
    assert row["tweak_count"] == 3
    assert (row["words_added"], row["words_removed"]) == (8, 3)
    assert (row["manual_count"], row["ai_count"], row["other_count"], row["restore_count"]) == (2, 1, 0, 0)
    assert row["sections_touched"] == 2
    assert row["opened_by_count"] == 2
    assert row["last_opened_by"] == B
    assert row["deleted"] is False


def test_restore_records_excluded_from_totals(db):
    _tweak(A, added=4)
    _tweak(A, source="reset", bucket="restore", section="s2", removed=4000, kind="removed")
    row = _only(ledger.list_rfp_ledger())
    assert row["words_removed"] == 0 and row["words_added"] == 4
    assert row["tweak_count"] == 1 and row["restore_count"] == 1
    assert row["sections_touched"] == 1


def test_deleted_rfp_stays_listed_marked(db):
    _row("rfp.created", A)
    _row("rfp.deleted", C)
    row = _only(ledger.list_rfp_ledger())
    assert row["deleted"] is True
    assert row["deleted_by"] == C
    assert row["deleted_at"]
    assert row["title"] == "Water Plant"
    assert ledger.list_rfp_ledger(include_deleted=False)["items"] == []


def test_run_groups_phases_and_outcome(db):
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-2"})
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-3", "chained": True})
    _row("proposal.phase_completed", A, run_id="L1", outcome="completed", meta={"phase": "phase-3"})
    _row("proposal.phase_started", B, run_id="L2", outcome="started", meta={"phase": "fulfill-scan"})
    detail = ledger.get_rfp_ledger_detail("r1")
    by_id = {r["run_id"]: r for r in detail["runs"]}
    assert by_id["L1"]["owner"] == A and by_id["L1"]["kind"] == "proposal"
    assert [p["phase"] for p in by_id["L1"]["phases"]] == ["phase-2", "phase-3"]
    assert by_id["L1"]["outcome"] == "completed"
    assert by_id["L1"]["finished_at"] and by_id["L1"]["duration_sec"] is not None
    assert by_id["L2"]["outcome"] == "running" and by_id["L2"]["finished_at"] is None


def test_gonogo_run(db):
    _row("rfp.gonogo_started", A, run_id="G1", outcome="started")
    _row("rfp.gonogo_completed", A, run_id="G1", outcome="completed", meta={"llm_run_id": "g-llm"})
    run = ledger.get_rfp_ledger_detail("r1")["runs"][0]
    assert (run["kind"], run["owner"], run["outcome"]) == ("gonogo", A, "completed")


def test_run_cost_sums_distinct_llm_run_ids(db, monkeypatch):
    prices = {"l1": 0.5, "l2": 0.25}
    monkeypatch.setattr(ledger, "get_run_total_cost_usd", lambda run_id: prices.get(run_id, 0.0))
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-2", "llm_run_id": "l1"})
    _row("proposal.phase_completed", A, run_id="L1", outcome="completed", meta={"phase": "phase-2", "llm_run_id": "l1"})
    _row("proposal.phase_completed", A, run_id="L1", outcome="completed", meta={"phase": "phase-3", "llm_run_id": "l2"})
    detail = ledger.get_rfp_ledger_detail("r1")
    assert detail["runs"][0]["cost_usd"] == pytest.approx(0.75)
    assert detail["rfp"]["cost_usd"] == pytest.approx(0.75)


def test_stopped_by_recorded_when_different(db):
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-3"})
    _row("proposal.phase_stopped", B, run_id="L1", outcome="cancelled", meta={"phase": "phase-3"})
    run = ledger.get_rfp_ledger_detail("r1")["runs"][0]
    assert run["outcome"] == "cancelled"
    assert run["stopped_by"] == B


def test_stopped_by_owner_is_not_reported(db):
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-3"})
    _row("proposal.phase_stopped", A, run_id="L1", outcome="cancelled", meta={"phase": "phase-3"})
    assert ledger.get_rfp_ledger_detail("r1")["runs"][0]["stopped_by"] is None


def test_detail_omits_text_and_opens_by_default(db):
    _tweak(A, added=2, instruction="tighten")
    _row("rfp.opened", B)
    detail = ledger.get_rfp_ledger_detail("r1")
    assert detail["opens"] == []
    assert [v["actor_email"] for v in detail["viewers"]] == [B]
    assert detail["viewers"][0]["open_count"] == 1
    assert "SECRET" not in repr(detail)
    assert detail["tweaks"][0]["has_instruction"] is True
    with_opens = ledger.get_rfp_ledger_detail("r1", include_opens=True)
    assert [o["actor_email"] for o in with_opens["opens"]] == [B]


def test_detail_for_unknown_rfp_is_empty_not_error(db):
    detail = ledger.get_rfp_ledger_detail("nope")
    assert detail == {"rfp": None, "runs": [], "tweaks": [], "viewers": [], "opens": []}


def test_person_filter_matches_runners_tweakers_openers_and_deleters(db):
    _row("proposal.phase_started", A, rfp="r1", run_id="L1", outcome="started", meta={"phase": "phase-2"})
    _tweak(B, rfp="r2")
    _row("rfp.opened", C, rfp="r3")
    assert [r["rfp_id"] for r in ledger.list_rfp_ledger(person=A)["items"]] == ["r1"]
    assert [r["rfp_id"] for r in ledger.list_rfp_ledger(person=B)["items"]] == ["r2"]
    assert [r["rfp_id"] for r in ledger.list_rfp_ledger(person=C)["items"]] == ["r3"]


def test_non_rfp_entities_are_ignored(db):
    emit_activity(workspace="rfp", action="kb.doc_deleted", summary="x", actor_email=A,
                  entity_type="kb_doc", entity_id="doc-1")
    assert ledger.list_rfp_ledger()["items"] == []


def test_people_view_counts(db):
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-2"})
    _tweak(A, added=2)
    _tweak(B, rfp="r2", added=1)
    _row("rfp.opened", B)
    _row("rfp.deleted", C, rfp="r2")
    people = {p["actor_email"]: p for p in ledger.list_people()["items"]}
    assert (people[A]["rfps_ran"], people[A]["runs"], people[A]["tweaks"], people[A]["rfps_tweaked"]) == (1, 1, 1, 1)
    assert (people[B]["rfps_opened"], people[B]["rfps_tweaked"]) == (1, 1)
    assert people[C]["rfps_deleted"] == 1
    assert "system" not in people


def test_endpoints_serve_ledger_and_gate_text_to_one_route(db):
    tweak_id = _tweak(A, added=2, instruction="tighten the opening")
    _row("proposal.phase_started", A, run_id="L1", outcome="started", meta={"phase": "phase-2"})
    client = TestClient(app)

    listing = client.get("/api/v1/activity/rfps")
    assert listing.status_code == 200 and listing.json()["items"][0]["rfp_id"] == "r1"

    detail = client.get("/api/v1/activity/rfps/r1")
    assert detail.status_code == 200
    assert "SECRET" not in detail.text and "tighten the opening" not in detail.text

    full = client.get(f"/api/v1/activity/tweaks/{tweak_id}")
    assert full.status_code == 200
    body = full.json()
    assert (body["before_text"], body["after_text"]) == ("SECRET OLD TEXT", "SECRET NEW TEXT")
    assert body["instruction"] == "tighten the opening"

    assert client.get("/api/v1/activity/tweaks/does-not-exist").status_code == 404
    assert client.get("/api/v1/activity/people").status_code == 200


def test_other_company_accounts_are_shown_as_system(db):
    cgn = "ai@contractorgrowthnetwork.com"
    _row("rfp.gonogo_started", cgn, run_id="G1", outcome="started")
    _row("rfp.gonogo_completed", cgn, run_id="G1", outcome="completed")
    _tweak(cgn, added=3)
    _row("rfp.opened", cgn)

    row = _only(ledger.list_rfp_ledger())
    assert row["ran_by"] == ["system"]
    assert row["last_opened_by"] == "system"
    assert cgn not in str(ledger.get_rfp_ledger_detail("r1"))
    assert [p["actor_email"] for p in ledger.list_people()["items"]] == []
