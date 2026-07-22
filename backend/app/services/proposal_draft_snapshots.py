"""Point-in-time copies of proposal sections (before Scan RFP, chat edits, etc.)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalDraftSnapshot

_MAX_SNAPSHOTS = 8

# These labels clutter the menu and/or restore empty pre-chat states (data loss UX).
_HIDDEN_SNAPSHOT_LABEL = re.compile(
    r"^(before\s+(restore|snapshot\s+restore|archive\s+restore|chat\s+edit)|"
    r"undo\s+point\s+before)\b",
    re.IGNORECASE,
)


def filled_count(draft: ProposalDraft | None) -> int:
    if not draft:
        return 0
    return sum(1 for s in draft.sections or [] if (s.content or "").strip())


def is_user_facing_snapshot_label(label: str | None) -> bool:
    text = (label or "").strip()
    if not text:
        return False
    return _HIDDEN_SNAPSHOT_LABEL.search(text) is None


def prune_clutter_snapshots(draft: ProposalDraft) -> ProposalDraft:
    """Remove restore-spam and pre-chat undo points from the version menu."""
    snaps = list(draft.snapshots or [])
    if not snaps:
        return draft
    kept = [s for s in snaps if is_user_facing_snapshot_label(s.label)]
    # Drop consecutive duplicates with the same label (same second spam).
    deduped: list[ProposalDraftSnapshot] = []
    for snap in kept:
        if deduped and (deduped[-1].label or "") == (snap.label or ""):
            # Keep the newer of the pair.
            deduped[-1] = snap
            continue
        deduped.append(snap)
    if len(deduped) > _MAX_SNAPSHOTS:
        deduped = deduped[-_MAX_SNAPSHOTS:]
    if deduped == snaps:
        return draft
    cleaned = draft.model_copy(update={"snapshots": deduped})
    # If we stripped only junk and nothing useful remains, keep one recoverable
    # checkpoint of the live manuscript so the Saved version menu stays useful.
    if not deduped and filled_count(cleaned) > 0:
        now = datetime.now(timezone.utc).isoformat()
        checkpoint = ProposalDraftSnapshot(
            saved_at=now,
            label="Saved draft",
            sections=[s.model_copy(deep=True) for s in cleaned.sections],
        )
        return cleaned.model_copy(
            update={"snapshots": [checkpoint], "updated_at": now}
        )
    return cleaned


def push_proposal_snapshot(
    draft: ProposalDraft,
    *,
    label: str,
) -> ProposalDraft:
    cleaned = prune_clutter_snapshots(draft)
    if not is_user_facing_snapshot_label(label):
        # Never add undo/restore spam into the compare menu.
        return cleaned
    now = datetime.now(timezone.utc).isoformat()
    snap = ProposalDraftSnapshot(
        saved_at=now,
        label=label,
        sections=[s.model_copy(deep=True) for s in cleaned.sections],
    )
    existing = [s for s in (cleaned.snapshots or []) if (s.label or "") != label]
    existing.append(snap)
    if len(existing) > _MAX_SNAPSHOTS:
        existing = existing[-_MAX_SNAPSHOTS:]
    return cleaned.model_copy(update={"snapshots": existing, "updated_at": now})


def push_after_section_edit_snapshot(
    draft: ProposalDraft,
    *,
    section_title: str,
) -> ProposalDraft:
    """Checkpoint the post-chat manuscript (the version users expect to keep)."""
    title = (section_title or "section").strip()[:48] or "section"
    cleaned = prune_clutter_snapshots(draft)
    return push_proposal_snapshot(cleaned, label=f"Saved after chat — {title}")


def attach_scan_summary_to_latest_before_scan(
    draft: ProposalDraft,
    report: dict[str, Any],
) -> ProposalDraft:
    """Link fulfill report to the 'Before Scan RFP' snapshot for UI compare."""
    snaps = list(draft.snapshots or [])
    if not snaps:
        return draft
    for i in range(len(snaps) - 1, -1, -1):
        if "before scan" in (snaps[i].label or "").casefold():
            snaps[i] = snaps[i].model_copy(update={"scan_summary": report})
            return draft.model_copy(update={"snapshots": snaps})
    snaps[-1] = snaps[-1].model_copy(update={"scan_summary": report})
    return draft.model_copy(update={"snapshots": snaps})


def restore_proposal_snapshot(
    draft: ProposalDraft,
    *,
    saved_at: str,
) -> ProposalDraft | None:
    for snap in draft.snapshots or []:
        if snap.saved_at != saved_at:
            continue
        # Slim / corrupted snapshots have empty section bodies — never wipe live draft.
        if not any((s.content or "").strip() for s in snap.sections or []):
            return None
        now = datetime.now(timezone.utc).isoformat()
        return draft.model_copy(
            update={
                "sections": [s.model_copy(deep=True) for s in snap.sections],
                "updated_at": now,
            }
        )
    return None
