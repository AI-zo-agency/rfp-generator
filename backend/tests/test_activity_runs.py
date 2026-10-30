"""Runs: one user click = one ledger run, shared by chained phases, owned by the clicker."""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import activity_runs as runs
from app.services.user_activity import emit_activity, list_activity


@pytest.fixture()
def db(sqlite_activity_store):
    return sqlite_activity_store


def _started(phase: str, run_id: str, rfp_id: str = "r1") -> None:
    emit_activity(
        workspace="rfp",
        action="proposal.phase_started",
        summary=f"Started {phase}",
        entity_type="rfp",
        entity_id=rfp_id,
        metadata={"phase": phase},
        run_id=run_id,
        outcome="started",
    )


def _rows(action: str) -> list[dict]:
    return list_activity(workspace="rfp", action=action)["items"]


def test_resolve_reuses_run_when_moving_forward(db):
    _started("phase-2", "L1")
    assert runs.resolve_ledger_run_id("r1", "phase-3") == "L1"


def test_resolve_new_run_for_same_or_earlier_phase(db):
    _started("phase-3", "L1")
    assert runs.resolve_ledger_run_id("r1", "phase-3") != "L1"
    assert runs.resolve_ledger_run_id("r1", "phase-2") != "L1"


def test_resolve_new_run_after_12_hours(db):
    _started("phase-2", "L1")
    later = datetime.now(timezone.utc) + timedelta(hours=13)
    assert runs.resolve_ledger_run_id("r1", "phase-3", now=later) != "L1"


def test_resolve_new_run_for_non_pipeline_phase(db):
    _started("phase-2", "L1")
    assert runs.resolve_ledger_run_id("r1", "fulfill-scan") != "L1"


def test_resolve_new_run_for_other_rfp(db):
    _started("phase-2", "L1", rfp_id="other")
    assert runs.resolve_ledger_run_id("r1", "phase-3") != "L1"


def test_scope_sets_actor_for_emit(db):
    with runs.ledger_run_scope(ledger_run_id="L", owner_email="o@zo.agency"):
        emit_activity(workspace="rfp", action="x.y", summary="no explicit actor")
    assert _rows("x.y")[0]["actor_email"] == "o@zo.agency"


def test_scope_restores_previous_values(db):
    assert runs.get_ledger_run_id() == ""
    with runs.ledger_run_scope(ledger_run_id="L", llm_run_id="llm1"):
        assert runs.get_ledger_run_id() == "L"
        assert runs.get_ledger_llm_run_id() == "llm1"
    assert runs.get_ledger_run_id() == ""
    assert runs.get_ledger_llm_run_id() == ""


def test_emit_pipeline_outcome_inside_scope_carries_owner_and_run(db):
    from app.services.proposal_pipeline_checkpoint import _emit_pipeline_outcome

    with runs.ledger_run_scope(ledger_run_id="L", owner_email="o@zo.agency", llm_run_id="llm1"):
        _emit_pipeline_outcome("r1", phase="phase-3", outcome="completed", summary="done")
    row = _rows("proposal.phase_completed")[0]
    assert row["actor_email"] == "o@zo.agency"
    assert row["run_id"] == "L"
    assert row["metadata"]["llm_run_id"] == "llm1"


def test_emit_pipeline_outcome_after_stop_uses_latest_started_run(db):
    from app.services.proposal_pipeline_checkpoint import _emit_pipeline_outcome

    _started("phase-3", "L2")
    _emit_pipeline_outcome("r1", phase="phase-3", outcome="cancelled", summary="stopped")
    assert _rows("proposal.phase_stopped")[0]["run_id"] == "L2"


def test_emit_phase_started_row(db):
    runs.emit_phase_started(
        "r1", "phase-3", title="Water Plant", ledger_run_id="L",
        owner_email="o@zo.agency", llm_run_id="llm1", chained=True,
    )
    row = _rows("proposal.phase_started")[0]
    assert (row["actor_email"], row["run_id"], row["outcome"]) == ("o@zo.agency", "L", "started")
    assert row["metadata"] == {"phase": "phase-3", "llm_run_id": "llm1", "chained": True}


def _go_no_go_patches(monkeypatch, analyze):
    monkeypatch.setattr("app.services.go_no_go_service.analyze_rfp", analyze)
    monkeypatch.setattr(
        "app.services.rfp_repository.get_rfp",
        lambda _id: SimpleNamespace(id="r1", title="Water Plant"),
    )
    monkeypatch.setattr(
        "app.services.rfp_repository.save_go_no_go_analysis", lambda _id, _a: object()
    )


def test_dispatch_go_no_go_emits_completed_with_owner(db, monkeypatch):
    from app.celery_app import _dispatch_go_no_go

    async def analyze(_rfp):
        return SimpleNamespace(recommendation="GO")

    _go_no_go_patches(monkeypatch, analyze)
    asyncio.run(_dispatch_go_no_go("r1", user_email="o@zo.agency", ledger_run_id="L"))
    row = _rows("rfp.gonogo_completed")[0]
    assert (row["actor_email"], row["run_id"]) == ("o@zo.agency", "L")
    assert row["metadata"]["recommendation"] == "GO"
    assert row["metadata"]["llm_run_id"]


def test_dispatch_go_no_go_emits_failed_and_reraises(db, monkeypatch):
    from app.celery_app import _dispatch_go_no_go

    async def analyze(_rfp):
        raise RuntimeError("boom")

    _go_no_go_patches(monkeypatch, analyze)
    with pytest.raises(RuntimeError):
        asyncio.run(_dispatch_go_no_go("r1", user_email="o@zo.agency", ledger_run_id="L"))
    row = _rows("rfp.gonogo_failed")[0]
    assert (row["actor_email"], row["run_id"], row["outcome"]) == ("o@zo.agency", "L", "failed")
    assert "boom" in row["metadata"]["error"]


def test_chained_phase_gets_started_row_with_same_ledger_id(db, monkeypatch):
    import app.celery_app as celery_app

    queued: list[tuple] = []

    async def no_cancel(_rfp_id):
        return None

    async def no_record(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.proposal_generation_cancel.check_generation_cancelled", no_cancel)
    monkeypatch.setattr("app.services.proposal_pipeline_checkpoint.record_phase_started", no_record)
    monkeypatch.setattr(celery_app, "settings", SimpleNamespace(celery_enabled=False))
    monkeypatch.setattr(
        celery_app.run_pipeline_phase_task,
        "delay",
        lambda rfp_id, phase, kwargs: queued.append((rfp_id, phase, kwargs)) or SimpleNamespace(id="t1"),
    )
    asyncio.run(
        celery_app._enqueue_next_generate_phase(
            "r1", "phase-2", user_email="o@zo.agency", ledger_run_id="L"
        )
    )
    row = _rows("proposal.phase_started")[0]
    assert (row["run_id"], row["actor_email"], row["metadata"]["phase"]) == ("L", "o@zo.agency", "phase-3")
    assert row["metadata"]["chained"] is True
    assert queued[0][2]["ledger_run_id"] == "L"
    assert queued[0][2]["user_email"] == "o@zo.agency"


def test_dispatch_phase_attributes_outcome_rows_to_owner(db, monkeypatch):
    """pipeline_phase emits its outcome row outside the llm context: the scope must wrap it."""
    import app.celery_app as celery_app
    from app.services.proposal_pipeline_checkpoint import _emit_pipeline_outcome

    async def fake_phase(_rfp_id, **_kw):
        return None

    mod = types.ModuleType("fake_phase_mod")
    mod.fake_phase = fake_phase
    monkeypatch.setitem(sys.modules, "fake_phase_mod", mod)
    monkeypatch.setitem(celery_app._PHASE_DISPATCH, "phase-2", ("fake_phase_mod", "fake_phase"))

    @asynccontextmanager
    async def fake_pipeline_phase(rfp_id, phase):
        yield
        _emit_pipeline_outcome(rfp_id, phase=phase, outcome="completed", summary="done")

    async def no_clear(_rfp_id):
        return None

    monkeypatch.setattr("app.services.proposal_pipeline_checkpoint.pipeline_phase", fake_pipeline_phase)
    monkeypatch.setattr("app.services.proposal_generation_cancel.aclear_generation_cancel", no_clear)
    asyncio.run(
        celery_app._dispatch_phase(
            "r1", "phase-2",
            {"chain_next": False, "user_email": "o@zo.agency", "ledger_run_id": "L"},
        )
    )
    row = _rows("proposal.phase_completed")[0]
    assert (row["actor_email"], row["run_id"]) == ("o@zo.agency", "L")
    assert row["metadata"]["llm_run_id"]
