"""Proposal Checklister — same completeness path as dynamic_section_planner.

Uses targeted submission/closing excerpts + dual-sampled missing-submittals
check (not a blind head-truncate of the RFP). Demo outline and Phase 2 both
call this.
"""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.plan_ops import append_decision
from app.services.proposal_intelligence.schemas import OutlineSection, ProposalExecutionPlan
from app.services.proposal_outline_dedup import humanize_outline_title

logger = logging.getLogger(__name__)
AGENT = "proposal_checklister"


async def run_proposal_checklister(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    """Audit outline for missing required submittals (forms / exhibits / attachments)."""
    _ = rfp_meta
    if not plan.writing.proposal_outline.sections:
        return plan

    from app.services.proposal_evaluation_coverage import ensure_missing_submittals_coverage
    from app.services.proposal_fulfill_rfp_structure import (
        title_is_rfp_instruction_not_deliverable,
    )

    before_ids = {s.id for s in plan.writing.proposal_outline.sections}
    kept, added_titles = await ensure_missing_submittals_coverage(
        list(plan.writing.proposal_outline.sections),
        rfp_context,
        section_factory=lambda raw: OutlineSection.model_validate(raw),
    )

    # Drop instruction-shaped injections; humanize long RFP headings on new tabs.
    cleaned: list[OutlineSection] = []
    kept_added = 0
    for sec in kept:
        if sec.id in before_ids:
            cleaned.append(sec)
            continue
        original = (sec.title or "").strip()
        if not original or title_is_rfp_instruction_not_deliverable(original):
            logger.info("%s dropped instruction-shaped tab %r", AGENT, original[:80])
            continue
        short = humanize_outline_title(original) or original
        if short != original:
            reason = (sec.conditional_reason or "").strip()
            full = f"Full RFP requirement: {original}"
            sec = sec.model_copy(
                update={
                    "title": short,
                    "conditional_reason": (
                        f"{full}\n\n{reason}".strip() if reason else full
                    ),
                }
            )
        cleaned.append(sec)
        kept_added += 1

    plan.writing.proposal_outline.sections = cleaned
    if kept_added:
        logger.info(
            "%s injected %d missing submittal tab(s) via completeness excerpts: %s",
            AGENT,
            kept_added,
            added_titles[:12],
        )

    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=(
            f"Checklister completeness: added {kept_added} missing submittal(s)."
            if kept_added
            else "Checklister completeness: 0 missing submittals detected."
        ),
        reason=(
            "Submission/closing excerpt dual-sample (same as ensure_missing_submittals_coverage)."
        ),
        confidence=0.95 if kept_added else 1.0,
    )
    return plan
