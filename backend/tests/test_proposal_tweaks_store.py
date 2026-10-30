"""proposal_tweak_events persistence (SQLite path; the Supabase path mirrors it)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import proposal_tweaks_store as store


def _record(**over) -> dict:
    base = {
        "rfp_id": "rfp-1",
        "rfp_title": "Water Plant",
        "section_id": "s1",
        "section_title": "Approach",
        "actor_email": "a@zo.agency",
        "source": "manual_edit",
        "bucket": "manual",
        "kind": "text",
        "instruction": "",
        "words_before": 3,
        "words_after": 4,
        "words_added": 2,
        "words_removed": 1,
        "before_title": "Approach",
        "after_title": "Approach",
        "before_text": "old text here",
        "after_text": "new text here too",
        "metadata": {},
    }
    base.update(over)
    return base


@pytest.fixture()
def db(sqlite_activity_store):
    return sqlite_activity_store


def test_insert_and_get_roundtrip(db):
    tweak_id = store.insert_tweak(_record(instruction="shorten it"))
    assert tweak_id
    got = store.get_tweak(tweak_id)
    assert got is not None
    for key, value in _record(instruction="shorten it").items():
        assert got[key] == value
    assert got["has_instruction"] is True
    assert got["created_at"] and got["updated_at"]


def test_get_unknown_returns_none(db):
    assert store.get_tweak("nope") is None


def test_cap_text_limits_bytes_and_keeps_valid_utf8():
    capped = store.cap_text("é" * 150_000)
    assert len(capped.encode("utf-8")) <= store.MAX_TEXT_BYTES
    assert capped.encode("utf-8").decode("utf-8") == capped
    assert store.cap_text("short") == "short"


def test_insert_caps_stored_text(db):
    tweak_id = store.insert_tweak(_record(after_text="x" * 300_000))
    assert len(store.get_tweak(tweak_id)["after_text"].encode("utf-8")) == store.MAX_TEXT_BYTES


def test_list_tweaks_omits_text(db):
    store.insert_tweak(_record(instruction="shorten it"))
    store.insert_tweak(_record(section_id="s2"))
    rows = store.list_tweaks(rfp_id="rfp-1")
    assert len(rows) == 2
    for row in rows:
        assert "before_text" not in row
        assert "after_text" not in row
        assert "instruction" not in row
    assert sorted(r["has_instruction"] for r in rows) == [False, True]


def test_list_filters_by_rfp_and_actor(db):
    store.insert_tweak(_record())
    store.insert_tweak(_record(rfp_id="rfp-2"))
    store.insert_tweak(_record(actor_email="b@zo.agency"))
    assert len(store.list_tweaks(rfp_id="rfp-1")) == 2
    assert len(store.list_tweaks(actor_email="b@zo.agency")) == 1
    assert len(store.list_tweaks()) == 3


def test_find_open_burst_respects_since(db):
    tweak_id = store.insert_tweak(_record())
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(minutes=11)).isoformat()
    assert store.update_tweak(tweak_id, {"updated_at": stale})
    since = (now - timedelta(minutes=10)).isoformat()
    assert store.find_open_burst("rfp-1", "s1", "a@zo.agency", since) is None
    fresh = (now - timedelta(minutes=5)).isoformat()
    assert store.update_tweak(tweak_id, {"updated_at": fresh})
    found = store.find_open_burst("rfp-1", "s1", "a@zo.agency", since)
    assert found is not None and found["id"] == tweak_id
    assert found["before_text"] == "old text here"


def test_find_open_burst_ignores_other_sources_and_people(db):
    store.insert_tweak(_record(source="chat_apply", bucket="ai"))
    store.insert_tweak(_record(actor_email="b@zo.agency"))
    since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert store.find_open_burst("rfp-1", "s1", "a@zo.agency", since) is None


def test_update_tweak_changes_allowed_fields_only(db):
    tweak_id = store.insert_tweak(_record())
    assert store.update_tweak(
        tweak_id,
        {"after_text": "newer", "words_after": 1, "actor_email": "evil@x.com", "before_text": "hacked"},
    )
    got = store.get_tweak(tweak_id)
    assert got["after_text"] == "newer"
    assert got["words_after"] == 1
    assert got["actor_email"] == "a@zo.agency"
    assert got["before_text"] == "old text here"
