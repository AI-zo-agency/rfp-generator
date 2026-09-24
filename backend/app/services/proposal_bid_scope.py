"""Bid-scope helpers for multi-track / multi-role RFPs.

Matching uses exact Fit track label strings. Empty track = shared scope.
"""

from __future__ import annotations

from typing import Any, Iterable, TypeVar

T = TypeVar("T")


def _track_of(obj: Any) -> str:
    raw = getattr(obj, "track", None)
    if raw is None and isinstance(obj, dict):
        raw = obj.get("track") or obj.get("Track")
    return str(raw or "").strip()


def tracks_from_capability_rows(rows: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows or []:
        track = _track_of(row)
        if not track or track in seen:
            continue
        seen.add(track)
        out.append(track)
    return out


def track_in_bid_scope(track: str, selected_tracks: list[str] | None) -> bool:
    label = (track or "").strip()
    if not selected_tracks:
        return True
    if not label:
        return True
    return label in selected_tracks


def filter_capability_rows_for_bid_scope(
    rows: Iterable[T], selected_tracks: list[str] | None
) -> list[T]:
    return [r for r in (rows or []) if track_in_bid_scope(_track_of(r), selected_tracks)]


def filter_outline_sections_for_bid_scope(
    sections: Iterable[T], selected_tracks: list[str] | None
) -> list[T]:
    if not selected_tracks:
        return list(sections or [])
    return [
        s for s in (sections or []) if track_in_bid_scope(_track_of(s), selected_tracks)
    ]


def bid_scope_prompt_block(selected_tracks: list[str]) -> str:
    if not selected_tracks:
        return ""
    listed = ", ".join(selected_tracks)
    return (
        "BID SCOPE (HARD CONSTRAINT):\n"
        f"This proposal bids ONLY the following RFP track(s)/role(s): {listed}.\n"
        "Do NOT plan, draft, price, or require content that belongs exclusively to "
        "other tracks. Shared package requirements (insurance, forms, submission "
        "rules, cover) with no track label remain in scope.\n"
        "Use the selected role's page limit and submission checklist when the RFP "
        "states them per role — not a merged multi-role package.\n"
    )


def bid_scope_requires_lock(available_tracks: list[str]) -> bool:
    return len(available_tracks or []) >= 2


def bid_scope_is_locked(
    locked_at: str | None, available_tracks: list[str]
) -> bool:
    if not bid_scope_requires_lock(available_tracks):
        return True
    return bool(locked_at)


def assert_bid_scope_ready_for_generate(
    *,
    available_tracks: list[str],
    selected_tracks: list[str] | None,
    bid_scope_locked_at: str | None,
) -> None:
    if not bid_scope_requires_lock(available_tracks):
        return
    if not bid_scope_locked_at:
        raise ValueError(
            "Bid scope is required before Build: this RFP has multiple tracks. "
            "Lock which track(s) to bid on the RFP detail page."
        )
    if not selected_tracks:
        raise ValueError(
            "Bid scope lock is missing selected_tracks. Re-confirm bid scope."
        )


def available_tracks_from_rfp_analysis(analysis: Any) -> list[str]:
    """Read availableTracks from a Go/No-Go analysis dict or model."""
    if analysis is None:
        return []
    if not isinstance(analysis, dict):
        tracks = getattr(analysis, "available_tracks", None)
        if tracks:
            return list(tracks)
        rows = getattr(analysis, "capability_matrix", None) or []
        return tracks_from_capability_rows(rows)
    raw = analysis.get("availableTracks") or analysis.get("available_tracks") or []
    if raw:
        return [str(t).strip() for t in raw if str(t).strip()]
    rows = analysis.get("capabilityMatrix") or analysis.get("capability_matrix") or []
    return tracks_from_capability_rows(rows)


def enforce_bid_scope_for_rfp(rfp: Any) -> None:
    """Raise ValueError when a multi-track RFP has not locked bid scope."""
    analysis = getattr(rfp, "go_no_go_analysis", None)
    available = available_tracks_from_rfp_analysis(analysis)
    assert_bid_scope_ready_for_generate(
        available_tracks=available,
        selected_tracks=list(getattr(rfp, "selected_tracks", None) or []),
        bid_scope_locked_at=getattr(rfp, "bid_scope_locked_at", None),
    )
