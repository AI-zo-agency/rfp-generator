# backend/tests/test_proposal_bid_scope.py
from types import SimpleNamespace

from app.services.proposal_bid_scope import (
    assert_bid_scope_ready_for_generate,
    bid_scope_prompt_block,
    bid_scope_requires_lock,
    filter_capability_rows_for_bid_scope,
    filter_outline_sections_for_bid_scope,
    track_in_bid_scope,
    tracks_from_capability_rows,
)


def test_tracks_from_capability_rows_preserves_order_and_skips_empty():
    rows = [
        SimpleNamespace(track="III.A VIC"),
        SimpleNamespace(track=""),
        SimpleNamespace(track="III.B VIS"),
        SimpleNamespace(track="III.A VIC"),
    ]
    assert tracks_from_capability_rows(rows) == ["III.A VIC", "III.B VIS"]


def test_shared_track_always_in_scope():
    assert track_in_bid_scope("", ["III.B VIS"]) is True
    assert track_in_bid_scope("III.A VIC", ["III.B VIS"]) is False
    assert track_in_bid_scope("III.B VIS", ["III.B VIS"]) is True
    assert track_in_bid_scope("III.A VIC", None) is True
    assert track_in_bid_scope("III.A VIC", []) is True


def test_filter_capability_rows_keeps_shared_and_selected():
    rows = [
        SimpleNamespace(requirement="Insurance", track=""),
        SimpleNamespace(requirement="Walk-in center", track="III.A VIC"),
        SimpleNamespace(requirement="Phone concierge", track="III.B VIS"),
    ]
    out = filter_capability_rows_for_bid_scope(rows, ["III.B VIS"])
    assert [r.requirement for r in out] == ["Insurance", "Phone concierge"]


def test_filter_outline_drops_other_track_keeps_blank():
    sections = [
        SimpleNamespace(title="Cover", track=""),
        SimpleNamespace(title="Facility", track="III.A VIC"),
        SimpleNamespace(title="VIS Services", track="III.B VIS"),
    ]
    out = filter_outline_sections_for_bid_scope(sections, ["III.B VIS"])
    assert [s.title for s in out] == ["Cover", "VIS Services"]


def test_prompt_block_names_exact_tracks():
    text = bid_scope_prompt_block(["III.B VIS"])
    assert "III.B VIS" in text
    assert "only" in text.lower()


def test_requires_lock_when_two_plus_tracks():
    assert bid_scope_requires_lock(["A", "B"]) is True
    assert bid_scope_requires_lock(["A"]) is False
    assert bid_scope_requires_lock([]) is False


def test_generate_gate_blocks_unlocked_multi_track():
    try:
        assert_bid_scope_ready_for_generate(
            available_tracks=["A", "B"],
            selected_tracks=[],
            bid_scope_locked_at=None,
        )
        raised = False
    except ValueError as exc:
        raised = True
        assert "bid scope" in str(exc).lower()
    assert raised is True


def test_generate_gate_allows_locked_subset():
    assert_bid_scope_ready_for_generate(
        available_tracks=["A", "B"],
        selected_tracks=["B"],
        bid_scope_locked_at="2026-09-23T10:00:00Z",
    )
