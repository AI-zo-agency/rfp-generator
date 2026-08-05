"""Merge helpers so research saves do not wipe durable analysis fields."""

from __future__ import annotations

import logging

from app.models.proposal import ProposalResearchCache

logger = logging.getLogger(__name__)


def merge_research_preserve_audit_fields(
    incoming: ProposalResearchCache,
    existing: ProposalResearchCache | None,
) -> ProposalResearchCache:
    """If incoming nulls a durable field, keep the existing value.

    Explicit non-null incoming values always win (fresh audit/repair after Phase 4,
    fresh ledger after a Phase 2 re-run).

    ``requirement_ledger`` (and the durable fields added alongside it — see below)
    are preserved here rather than at each call site because 30+ places construct
    ``ProposalResearchCache`` from a hand-written whitelist of prior fields (e.g.
    ``_generate_sections_1_3_inner`` and ``_persist_sections_1_3_partial`` in
    ``proposal_generator.py``, which forward ``rfpSections`` but nothing else).
    Patching those two would leave the next one free to silently wipe these
    fields again.

    ``pricing_rate_card`` matters most operationally: ``run_fulfill_budget_scan``
    (the "Scan RFP" path) reads it to build the underbid-floor check's rate card,
    and a missing card takes the ``rate_card = None`` branch, which by design
    never halts. Losing this field on a routine Sections 1-3 regeneration would
    silently turn off the underbid-floor protection.
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
    if incoming.requirement_ledger is None and existing.requirement_ledger is not None:
        updates["requirement_ledger"] = existing.requirement_ledger
    if incoming.pricing_rate_card is None and existing.pricing_rate_card is not None:
        updates["pricing_rate_card"] = existing.pricing_rate_card
    if incoming.manuscript_locks is None and existing.manuscript_locks is not None:
        updates["manuscript_locks"] = existing.manuscript_locks
    if incoming.evidence_allocation is None and existing.evidence_allocation is not None:
        updates["evidence_allocation"] = existing.evidence_allocation
    if not incoming.proof_points and existing.proof_points:
        updates["proof_points"] = existing.proof_points
    if not incoming.section_queries and existing.section_queries:
        updates["section_queries"] = existing.section_queries
    if not incoming.loss_lessons and existing.loss_lessons:
        updates["loss_lessons"] = existing.loss_lessons

    if not updates:
        return incoming

    logger.info(
        "research_merge preserve_fields=%s rfp_id=%s",
        sorted(updates.keys()),
        incoming.rfp_id,
    )
    return incoming.model_copy(update=updates)
