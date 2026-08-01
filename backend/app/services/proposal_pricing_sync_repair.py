"""Deterministic Phase 3.5d pricing sync retry + Sonja handoff (no LLM money rewrite)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.core.step_debug_logger import step_trace
from app.models.proposal import (
    BudgetNarrativeMismatch,
    PricingSyncReport,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_adversarial_repair import append_manual_fill_tag
from app.services.proposal_budget_content import reconcile_draft_budget_summaries
from app.services.proposal_budget_sync import (
    align_fee_narrative_with_budget,
    run_budget_grounding_check,
)

logger = logging.getLogger(__name__)

MAX_SYNC_ROUNDS = 2
MAX_HANDOFF_TAGS = 8

_FIELD_TO_CODE = {
    "agency_fee": "budget_grounding_agency_fee",
    "media_passthrough": "budget_grounding_media_passthrough",
    "direct_expenses": "budget_grounding_direct_expenses",
    "total_invoicing": "budget_grounding_total_invoicing",
    "rfp_ceiling_claim": "budget_grounding_invented_ceiling",
    "rfp_authority": "budget_grounding_rfp_authority",
}


def grounding_code_for_mismatch(mismatch: BudgetNarrativeMismatch) -> str:
    field = (mismatch.claimed_field or "").strip()
    return _FIELD_TO_CODE.get(field, "budget_grounding_mismatch")


def scrub_invented_ceiling_claims(
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> tuple[ProposalDraft, int]:
    """Remove sentences that label bid totals as RFP ceiling/allocation."""
    from app.services.evidence_trust.rfp_money_constraints import (
        collect_invented_ceiling_mismatches,
    )

    sections: list[ProposalSection] = []
    removed = 0
    for section in draft.sections:
        body = section.content or ""
        invented = collect_invented_ceiling_mismatches(
            body,
            budget=budget,
            section_id=section.id,
            section_title=section.title or "",
        )
        if not invented:
            sections.append(section)
            continue
        new_body = body
        for item in invented:
            sentence = (item.sentence or "").strip()
            if not sentence:
                continue
            pattern = re.escape(sentence)
            updated, n = re.subn(pattern, "", new_body, count=1)
            if n:
                removed += n
                new_body = updated
        new_body = re.sub(r"\n{3,}", "\n\n", new_body).strip()
        if new_body != (body or "").strip():
            sections.append(
                section.model_copy(update={"content": new_body, "status": "generated"})
            )
        else:
            sections.append(section)
    if removed <= 0:
        return draft, 0
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), removed


def rerender_budget_section_from_canon(
    draft: ProposalDraft,
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> ProposalDraft:
    """Replace the budget section with the canonical budget rendering."""
    from app.services.proposal_budget_content import (
        find_budget_section_index,
        render_budget_markdown,
    )

    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return draft
    content = render_budget_markdown(budget, rfp_text=rfp_text)
    sections = list(draft.sections)
    sections[idx] = sections[idx].model_copy(
        update={"content": content, "status": "generated"}
    )
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now})


def _mismatch_sample(mismatch: BudgetNarrativeMismatch) -> str:
    return ((mismatch.note or mismatch.sentence or "").strip())[:160]


def _apply_handoffs(
    draft: ProposalDraft,
    mismatches: list[BudgetNarrativeMismatch],
) -> tuple[ProposalDraft, list[str], int]:
    """Append bounded, code-first manual-fill tags for unresolved mismatches."""
    codes: list[str] = []
    seen: set[tuple[str, str]] = set()
    tag_count = 0

    for mismatch in mismatches:
        code = grounding_code_for_mismatch(mismatch)
        if code not in codes:
            codes.append(code)
        key = (code, (mismatch.section_id or "").strip())
        if key in seen or tag_count >= MAX_HANDOFF_TAGS:
            continue
        seen.add(key)
        issue = (
            mismatch.note
            or mismatch.sentence
            or f"Confirm canonical budget grounding for {mismatch.claimed_field or 'pricing'}."
        )
        draft, tag = append_manual_fill_tag(
            draft,
            section_id=mismatch.section_id,
            issue=issue,
            finding_code=code,
        )
        if tag is not None:
            tag_count += 1

    return draft, codes, tag_count


def _research_with_sync_result(
    research: ProposalResearchCache | None,
    *,
    budget: ProposalBudget,
    report: PricingSyncReport,
) -> ProposalResearchCache | None:
    if research is None:
        return None
    return research.model_copy(
        update={"budget": budget, "pricing_sync_report": report}
    )


async def run_pricing_sync_repair_or_handoff(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    budget: ProposalBudget,
    research: ProposalResearchCache | None,
    initial_mismatches: list[BudgetNarrativeMismatch],
    rfp_text: str = "",
) -> tuple[ProposalDraft, ProposalResearchCache | None, ProposalBudget, PricingSyncReport]:
    """Bounded canonical pricing sync, followed by an auditable handoff if needed."""
    mismatches = list(initial_mismatches)
    initial_samples = [_mismatch_sample(item) for item in mismatches[:3]]
    logger.info(
        "pricing_sync_repair_start",
        extra={
            "rfp_id": rfp_id,
            "mismatch_count": len(mismatches),
            "samples": initial_samples,
        },
    )
    step_trace(
        "pricing_sync_repair_start",
        rfp_id,
        mismatch_count=len(mismatches),
        samples=initial_samples,
    )

    if not mismatches:
        report = PricingSyncReport(
            roundsRun=0,
            resolved=True,
            handoff=False,
            mismatchCount=0,
        )
        budget = budget.model_copy(update={"narrative_mismatches": []})
        logger.info(
            "pricing_sync_repair_resolved",
            extra={"rfp_id": rfp_id, "rounds_run": 0},
        )
        step_trace(
            "pricing_sync_repair_resolved",
            rfp_id,
            rounds_run=0,
        )
        return (
            draft,
            _research_with_sync_result(research, budget=budget, report=report),
            budget,
            report,
        )

    for round_number in range(1, MAX_SYNC_ROUNDS + 1):
        before_count = len(mismatches)
        draft = rerender_budget_section_from_canon(draft, budget, rfp_text=rfp_text)
        draft, reconciled_count = reconcile_draft_budget_summaries(draft, budget)
        draft = await align_fee_narrative_with_budget(
            rfp_id=rfp_id,
            draft=draft,
            budget=budget,
        )
        draft, scrubbed_count = scrub_invented_ceiling_claims(draft, budget)
        mismatches = await run_budget_grounding_check(
            rfp_id=rfp_id,
            draft=draft,
            budget=budget,
        )
        samples = [_mismatch_sample(item) for item in mismatches[:3]]
        logger.info(
            "pricing_sync_repair_round",
            extra={
                "rfp_id": rfp_id,
                "round": round_number,
                "mismatch_count_before": before_count,
                "mismatch_count_after": len(mismatches),
                "reconciled_count": reconciled_count,
                "scrubbed_count": scrubbed_count,
                "samples": samples,
            },
        )
        step_trace(
            "pricing_sync_repair_round",
            rfp_id,
            round=round_number,
            mismatch_count_before=before_count,
            mismatch_count_after=len(mismatches),
            reconciled_count=reconciled_count,
            scrubbed_count=scrubbed_count,
            samples=samples,
        )
        if not mismatches:
            report = PricingSyncReport(
                roundsRun=round_number,
                resolved=True,
                mismatchCount=0,
            )
            budget = budget.model_copy(update={"narrative_mismatches": []})
            logger.info(
                "pricing_sync_repair_resolved",
                extra={"rfp_id": rfp_id, "rounds_run": round_number},
            )
            step_trace(
                "pricing_sync_repair_resolved",
                rfp_id,
                rounds_run=round_number,
            )
            return (
                draft,
                _research_with_sync_result(research, budget=budget, report=report),
                budget,
                report,
            )

    draft, codes, tag_count = _apply_handoffs(draft, mismatches)
    samples = [_mismatch_sample(item) for item in mismatches[:3]]
    report = PricingSyncReport(
        roundsRun=MAX_SYNC_ROUNDS,
        handoff=True,
        mismatchCount=len(mismatches),
        codes=codes,
        samples=samples,
    )
    budget = budget.model_copy(update={"narrative_mismatches": mismatches})
    logger.info(
        "pricing_sync_handoff",
        extra={
            "rfp_id": rfp_id,
            "rounds_run": MAX_SYNC_ROUNDS,
            "mismatch_count": len(mismatches),
            "codes": codes,
            "tag_count": tag_count,
            "samples": samples,
        },
    )
    step_trace(
        "pricing_sync_handoff",
        rfp_id,
        rounds_run=MAX_SYNC_ROUNDS,
        mismatch_count=len(mismatches),
        codes=codes,
        tag_count=tag_count,
        samples=samples,
    )
    return (
        draft,
        _research_with_sync_result(research, budget=budget, report=report),
        budget,
        report,
    )
