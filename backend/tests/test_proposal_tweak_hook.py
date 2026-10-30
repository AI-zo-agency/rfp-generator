"""The save/delete hook, the request classifier and the instruction memory."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_repository as repo
from app.services import proposal_tweaks as tweaks
from app.services import proposal_tweaks_store as store
from app.services.llm_call_context import llm_call_context

PREFIX = "/api/v1/rfps/abc"


def _draft(*sections: tuple[str, str], rfp_id: str = "r1") -> ProposalDraft:
    return ProposalDraft(
        rfp_id=rfp_id,
        sections=[ProposalSection(id=i, title=f"Title {i}", content=c) for i, c in sections],
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.fixture()
def db(sqlite_activity_store):
    repo.init_proposal_db()
    return sqlite_activity_store


def _person(source: str):
    """Context of an HTTP request by a person: a tweak source plus their email."""
    return _Both(tweaks.tweak_source_scope(source), llm_call_context(user_email="p@zo.agency"))


class _Both:
    def __init__(self, *managers):
        self._managers = managers

    def __enter__(self):
        for m in self._managers:
            m.__enter__()

    def __exit__(self, *exc):
        for m in reversed(self._managers):
            m.__exit__(*exc)


def test_save_with_manual_source_records_tweak(db):
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with _person("manual_edit"):
        repo.save_proposal_draft(_draft(("s1", "new text")))
    rows = store.list_tweaks(rfp_id="r1")
    assert len(rows) == 1
    assert rows[0]["actor_email"] == "p@zo.agency"
    assert rows[0]["source"] == "manual_edit"
    assert store.get_tweak(rows[0]["id"])["before_text"] == "old text"


def test_save_without_source_records_nothing(db):
    repo.save_proposal_draft(_draft(("s1", "old text")))
    repo.save_proposal_draft(_draft(("s1", "new text")))
    assert store.list_tweaks() == []


def test_save_with_run_source_records_nothing(db):
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with _person("run"):
        repo.save_proposal_draft(_draft(("s1", "new text")))
    assert store.list_tweaks() == []


def test_pipeline_node_name_with_stale_source_records_nothing(db):
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with tweaks.tweak_source_scope("manual_edit"), llm_call_context(
        user_email="p@zo.agency", node_name="phase-3"
    ):
        repo.save_proposal_draft(_draft(("s1", "new text")))
    assert store.list_tweaks() == []


def test_save_without_actor_records_nothing(db):
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with tweaks.tweak_source_scope("manual_edit"):
        repo.save_proposal_draft(_draft(("s1", "new text")))
    assert store.list_tweaks() == []


def test_table_repair_only_change_records_nothing(db):
    flat = "Intro\n\n| Item | Cost | |---|---| | A | 1 | | B | 2 |\n"
    repo.save_proposal_draft(_draft(("s1", flat)))  # stored in repaired form
    with _person("manual_edit"):
        repo.save_proposal_draft(_draft(("s1", flat)))  # same text, repaired again
    assert store.list_tweaks() == []


def test_recorder_failure_does_not_break_save(db, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("store is down")

    monkeypatch.setattr(tweaks, "record_tweaks", boom)
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with _person("manual_edit"):
        repo.save_proposal_draft(_draft(("s1", "new text")))
    assert repo.get_proposal_draft("r1").sections[0].content == "new text"


def test_delete_draft_with_reset_source_records_removals(db):
    repo.save_proposal_draft(_draft(("s1", "first body"), ("s2", "second body"), ("s3", "")))
    with _person("reset"):
        repo.delete_proposal_draft("r1")
    rows = store.list_tweaks(rfp_id="r1")
    assert sorted(r["section_id"] for r in rows) == ["s1", "s2"]
    assert {(r["kind"], r["bucket"], r["source"]) for r in rows} == {("removed", "restore", "reset")}
    assert repo.get_proposal_draft("r1") is None


def test_delete_without_source_records_nothing(db):
    repo.save_proposal_draft(_draft(("s1", "first body")))
    repo.delete_proposal_draft("r1")
    assert store.list_tweaks() == []


def test_chat_apply_records_the_typed_instruction(db):
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with _person("chat_apply"):
        tweaks.set_tweak_instruction("tighten the opening")
        repo.save_proposal_draft(_draft(("s1", "new text")))
    row = store.list_tweaks(rfp_id="r1")[0]
    assert store.get_tweak(row["id"])["instruction"] == "tighten the opening"
    assert row["bucket"] == "ai"


def test_chat_confirm_recalls_the_preview_instruction(db):
    tweaks.remember_preview_instruction("r1", "p@zo.agency", "make it shorter")
    repo.save_proposal_draft(_draft(("s1", "old text")))
    with _person("chat_confirm"):
        repo.save_proposal_draft(_draft(("s1", "new text")))
    row = store.list_tweaks(rfp_id="r1")[0]
    assert store.get_tweak(row["id"])["instruction"] == "make it shorter"


def test_preview_instruction_roundtrip_and_ttl():
    tweaks.remember_preview_instruction("r9", "a@zo.agency", "hello", now=1000.0)
    assert tweaks.recall_preview_instruction("r9", "a@zo.agency", now=1000.0 + 3599) == "hello"
    assert tweaks.recall_preview_instruction("r9", "a@zo.agency", now=1000.0 + 3601) == ""
    assert tweaks.recall_preview_instruction("r9", "other@zo.agency", now=1000.0) == ""


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("PUT", f"{PREFIX}/proposal", "manual_edit"),
        ("POST", f"{PREFIX}/proposal/key-personas", "personas"),
        ("POST", f"{PREFIX}/key-personas", "personas"),
        ("POST", f"{PREFIX}/proposal/sections/s1/improve", "chat_apply"),
        ("POST", f"{PREFIX}/proposal/confirm-preview", "chat_confirm"),
        ("POST", f"{PREFIX}/proposal/restore-snapshot", "restore_snapshot"),
        ("POST", f"{PREFIX}/proposal/archives/a1/restore", "restore_archive"),
        ("POST", f"{PREFIX}/proposal/restart-from-intelligence", "restart"),
        ("POST", f"{PREFIX}/proposal/restart-from-case-studies", "restart"),
        ("POST", f"{PREFIX}/proposal/reset", "reset"),
        ("POST", f"{PREFIX}/proposal/generate", "run"),
        ("POST", f"{PREFIX}/proposal/phase-4-review", "run"),
        ("POST", f"{PREFIX}/proposal/phase-4-auto-fix", "run"),
        ("POST", f"{PREFIX}/proposal/phase-3-5-budget-reconcile", "run"),
        ("POST", f"{PREFIX}/proposal/pricing/generate", "run"),
        ("POST", f"{PREFIX}/proposal/build-finalize", "run"),
        ("POST", f"{PREFIX}/proposal/fulfill-rfp-gaps", "run"),
        ("POST", f"{PREFIX}/proposal/align-rfp-outline", "run"),
        ("POST", f"{PREFIX}/proposal/align-rfp-outline/preview", "run"),
        ("POST", f"{PREFIX}/proposal/packet-redistribute", "run"),
        ("POST", f"{PREFIX}/proposal/packet-redistribute/preview", "run"),
        ("POST", f"{PREFIX}/proposal/match-case-studies", "run"),
        ("POST", f"{PREFIX}/proposal/export/docx", "run"),
        ("POST", f"{PREFIX}/proposal/export/google-doc", "run"),
        ("POST", f"{PREFIX}/proposal/stop", "run"),
        ("POST", f"{PREFIX}/proposal/generation/clear-stop", "run"),
        ("PUT", f"{PREFIX}/proposal/voice-rev", "run"),
        ("GET", f"{PREFIX}/proposal", ""),
        ("POST", f"{PREFIX}/proposal/something-new", "other"),
        ("DELETE", PREFIX, ""),
        ("POST", f"{PREFIX}/analyze", ""),
        ("POST", "/api/v1/activity/rfp-opened", ""),
    ],
)
def test_classify_proposal_request(method, path, expected):
    assert tweaks.classify_proposal_request(method, path) == expected


def test_middleware_sets_source_for_the_request():
    from app.main import attach_tweak_source

    app = FastAPI()
    app.middleware("http")(attach_tweak_source)

    @app.put("/api/v1/rfps/x/proposal")
    def put_proposal() -> dict[str, str]:
        return {"source": tweaks.get_tweak_source()}

    @app.get("/api/v1/rfps/x/proposal")
    def get_proposal() -> dict[str, str]:
        return {"source": tweaks.get_tweak_source()}

    client = TestClient(app)
    assert client.put("/api/v1/rfps/x/proposal").json() == {"source": "manual_edit"}
    assert client.get("/api/v1/rfps/x/proposal").json() == {"source": ""}
    assert tweaks.get_tweak_source() == ""
