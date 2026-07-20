"""Point-in-time copies of proposal sections (before Scan RFP, etc.)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalDraftSnapshot, ProposalSection

_MAX_SNAPSHOTS = 10


def push_proposal_snapshot(
    draft: ProposalDraft,
    *,
    label: str,
) -> ProposalDraft:
    now = datetime.now(timezone.utc).isoformat()
    snap = ProposalDraftSnapshot(
        saved_at=now,
        label=label,
        sections=[s.model_copy(deep=True) for s in draft.sections],
    )
    existing = list(draft.snapshots or [])
    existing.append(snap)
    if len(existing) > _MAX_SNAPSHOTS:
        existing = existing[-_MAX_SNAPSHOTS :]
    return draft.model_copy(update={"snapshots": existing, "updated_at": now})


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
        if snap.saved_at == saved_at:
            now = datetime.now(timezone.utc).isoformat()
            return draft.model_copy(
                update={
                    "sections": [s.model_copy(deep=True) for s in snap.sections],
                    "updated_at": now,
                }
            )
    return None
