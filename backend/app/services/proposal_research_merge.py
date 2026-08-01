"""Merge helpers so research saves do not wipe adversarial audit/repair."""

from __future__ import annotations

import logging

from app.models.proposal import ProposalResearchCache

logger = logging.getLogger(__name__)


def merge_research_preserve_audit_fields(
    incoming: ProposalResearchCache,
    existing: ProposalResearchCache | None,
) -> ProposalResearchCache:
    """If incoming nulls audit/repair, keep existing values.

    Explicit non-null incoming values always win (fresh audit/repair after Phase 4).
    """
    if existing is None:
        return incoming

    updates: dict = {}
    if incoming.adversarial_audit is None and existing.adversarial_audit is not None:
        updates["adversarial_audit"] = existing.adversarial_audit
    if (
        incoming.adversarial_repair_report is None
        and existing.adversarial_repair_report is not None
    ):
        updates["adversarial_repair_report"] = existing.adversarial_repair_report
    if incoming.pricing_sync_report is None and existing.pricing_sync_report is not None:
        updates["pricing_sync_report"] = existing.pricing_sync_report

    if not updates:
        return incoming

    logger.info(
        "research_merge preserve_fields=%s rfp_id=%s",
        sorted(updates.keys()),
        incoming.rfp_id,
    )
    return incoming.model_copy(update=updates)
