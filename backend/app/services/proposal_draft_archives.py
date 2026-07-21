"""Durable proposal draft archives — survive live-row delete / Reset."""

from __future__ import annotations

import logging
from typing import Any

from app.models.proposal import ProposalDraft
from app.services.proposal_draft_snapshots import push_proposal_snapshot

logger = logging.getLogger(__name__)

_MAX_ARCHIVES_PER_RFP = 20

REASON_BEFORE_SECTIONS_1_3_REGEN = "before_sections_1_3_regen"
REASON_BEFORE_RESET = "before_reset"
REASON_BEFORE_FORCE_RESTART = "before_force_restart"
REASON_BEFORE_ARCHIVE_RESTORE = "before_archive_restore"


def draft_has_filled_content(draft: ProposalDraft | None) -> bool:
    if not draft:
        return False
    return any((s.content or "").strip() for s in draft.sections or [])


def filled_section_count(draft: ProposalDraft) -> int:
    return sum(1 for s in draft.sections or [] if (s.content or "").strip())


def prepare_draft_for_archive(
    draft: ProposalDraft,
    *,
    label: str,
) -> ProposalDraft:
    """Ensure an in-payload snapshot exists, then return draft ready to archive."""
    if not draft_has_filled_content(draft):
        return draft
    return push_proposal_snapshot(draft, label=label)


async def archive_filled_draft(
    draft: ProposalDraft | None,
    *,
    reason: str,
    label: str | None = None,
) -> str | None:
    """Persist full payload to proposal_draft_archives. Returns archive id or None."""
    if not draft_has_filled_content(draft):
        return None
    assert draft is not None
    from app.services.proposal_repository import asave_proposal_draft_archive

    snap_label = label or reason.replace("_", " ")
    to_store = prepare_draft_for_archive(draft, label=snap_label)
    # Keep live draft snapshots in sync when we still have the row.
    try:
        from app.services.proposal_repository import asave_proposal_draft

        await asave_proposal_draft(to_store)
    except Exception:
        logger.exception(
            "Failed to persist in-draft snapshot before archive for %s",
            draft.rfp_id,
        )

    archive_id = await asave_proposal_draft_archive(
        rfp_id=draft.rfp_id,
        reason=reason,
        label=snap_label,
        payload=to_store,
    )
    logger.info(
        "Archived proposal draft rfp=%s reason=%s archive_id=%s filled=%d",
        draft.rfp_id,
        reason,
        archive_id,
        filled_section_count(to_store),
    )
    return archive_id


def archive_meta_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "rfpId": str(row.get("rfp_id") or ""),
        "archivedAt": str(row.get("archived_at") or ""),
        "reason": str(row.get("reason") or ""),
        "label": row.get("label"),
        "sectionCount": int(row.get("section_count") or 0),
        "filledCount": int(row.get("filled_count") or 0),
    }
