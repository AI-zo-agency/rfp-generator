"""Section diffing, word counts and tweak recording (no HTTP, no hook)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_tweaks as tweaks
from app.services import proposal_tweaks_store as store


def _draft(*sections: tuple[str, str, str], rfp_id: str = "r1") -> ProposalDraft:
    return ProposalDraft(
        rfp_id=rfp_id,
        sections=[ProposalSection(id=i, title=t, content=c) for i, t, c in sections],
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_whitespace_only_change_is_ignored():
    before = _draft(("s1", "Approach", "a  b\n"))
    after = _draft(("s1", "Approach", "a b"))
    assert tweaks.diff_drafts(before, after) == []


def test_text_change_produces_one_change():
    before = _draft(("s1", "Approach", "one two"), ("s2", "Team", "same"))
    after = _draft(("s1", "Approach", "one three"), ("s2", "Team", "same"))
    changes = tweaks.diff_drafts(before, after)
    assert [(c.section_id, c.kind) for c in changes] == [("s1", "text")]
    assert changes[0].before_text == "one two"
    assert changes[0].after_text == "one three"


def test_added_removed_renamed_sections():
    before = _draft(("s1", "Old name", "body one"), ("s2", "Gone", "going away"))
    after = _draft(("s1", "New name", "body one"), ("s3", "Fresh", "brand new"))
    by_id = {c.section_id: c for c in tweaks.diff_drafts(before, after)}
    assert by_id["s1"].kind == "renamed"
    assert (by_id["s1"].before_title, by_id["s1"].after_title) == ("Old name", "New name")
    assert by_id["s2"].kind == "removed" and by_id["s2"].after_text == ""
    assert by_id["s3"].kind == "added" and by_id["s3"].before_text == ""


def test_empty_outline_stubs_are_not_changes():
    after = _draft(("s9", "Stub", ""))
    assert tweaks.diff_drafts(None, after) == []
    assert tweaks.diff_drafts(after, None) == []


def test_none_before_treats_filled_sections_as_added():
    after = _draft(("s1", "Approach", "text"))
    changes = tweaks.diff_drafts(None, after)
    assert [(c.section_id, c.kind) for c in changes] == [("s1", "added")]


def test_count_words_changed():
    assert tweaks.count_words_changed("a b c", "a x c d") == (2, 1)
    assert tweaks.count_words_changed("same words", "same words") == (0, 0)


def test_count_words_changed_large_input_returns_fast():
    big_a = " ".join(f"a{i}" for i in range(30_000))
    big_b = " ".join(f"b{i}" for i in range(30_500))
    started = time.monotonic()
    added, removed = tweaks.count_words_changed(big_a, big_b)
    assert time.monotonic() - started < 2
    assert (added, removed) == (500, 0)


@pytest.fixture()
def db(sqlite_activity_store):
    return sqlite_activity_store


def _record(before, after, **over):
    kwargs = {
        "source": "chat_apply",
        "actor_email": "a@zo.agency",
        "rfp_id": "r1",
        "rfp_title": "Water Plant",
    }
    kwargs.update(over)
    return tweaks.record_tweaks(before, after, **kwargs)


def test_record_tweaks_writes_buckets_and_counts(db):
    before = _draft(("s1", "Approach", "one two three"))
    after = _draft(("s1", "Approach", "one four three five"))
    assert _record(before, after, instruction="make it concrete") == 1
    row = store.list_tweaks(rfp_id="r1")[0]
    assert row["bucket"] == "ai" and row["source"] == "chat_apply"
    assert (row["words_added"], row["words_removed"]) == (2, 1)
    assert (row["words_before"], row["words_after"]) == (3, 4)
    assert row["actor_email"] == "a@zo.agency"
    full = store.get_tweak(row["id"])
    assert full["instruction"] == "make it concrete"
    assert full["before_text"] == "one two three"


def test_record_tweaks_ignores_unknown_source_and_blank_actor(db):
    before = _draft(("s1", "Approach", "a"))
    after = _draft(("s1", "Approach", "b"))
    assert _record(before, after, source="bogus") == 0
    assert _record(before, after, actor_email="") == 0
    assert store.list_tweaks() == []


def test_manual_edits_within_10_minutes_extend_one_row(db):
    t0 = datetime.now(timezone.utc)
    a = _draft(("s1", "Approach", "old text"))
    b = _draft(("s1", "Approach", "new text"))
    c = _draft(("s1", "Approach", "newer text here"))
    assert _record(a, b, source="manual_edit", now=t0) == 1
    assert _record(b, c, source="manual_edit", now=t0 + timedelta(minutes=4)) == 1
    rows = store.list_tweaks(rfp_id="r1")
    assert len(rows) == 1
    full = store.get_tweak(rows[0]["id"])
    assert full["before_text"] == "old text"
    assert full["after_text"] == "newer text here"
    assert (full["words_before"], full["words_after"]) == (2, 3)
    # old -> newer is one removed + one added, and "here" is one more added.
    assert (full["words_added"], full["words_removed"]) == (2, 1)


def test_manual_edits_after_quiet_gap_start_a_new_row(db):
    t0 = datetime.now(timezone.utc)
    a = _draft(("s1", "Approach", "old text"))
    b = _draft(("s1", "Approach", "new text"))
    c = _draft(("s1", "Approach", "newer text"))
    _record(a, b, source="manual_edit", now=t0)
    _record(b, c, source="manual_edit", now=t0 + timedelta(minutes=11))
    assert len(store.list_tweaks(rfp_id="r1")) == 2


def test_manual_edits_by_different_people_never_merge(db):
    t0 = datetime.now(timezone.utc)
    a = _draft(("s1", "Approach", "old text"))
    b = _draft(("s1", "Approach", "new text"))
    c = _draft(("s1", "Approach", "newer text"))
    _record(a, b, source="manual_edit", now=t0)
    _record(b, c, source="manual_edit", actor_email="b@zo.agency", now=t0 + timedelta(minutes=1))
    assert len(store.list_tweaks(rfp_id="r1")) == 2


def test_non_manual_sources_never_merge(db):
    a = _draft(("s1", "Approach", "old text"))
    b = _draft(("s1", "Approach", "new text"))
    c = _draft(("s1", "Approach", "newer text"))
    _record(a, b, source="chat_apply")
    _record(b, c, source="chat_apply")
    assert len(store.list_tweaks(rfp_id="r1")) == 2


def test_after_text_is_capped(db):
    before = _draft(("s1", "Approach", "short"))
    after = _draft(("s1", "Approach", "w " * 150_000))
    _record(before, after)
    row = store.list_tweaks(rfp_id="r1")[0]
    assert len(store.get_tweak(row["id"])["after_text"].encode("utf-8")) <= store.MAX_TEXT_BYTES
