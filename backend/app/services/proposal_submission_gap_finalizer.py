"""Final editor pass — Supermemory KB query + deterministic tag fills, then MANUAL FILL handoff."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models.proposal import EvidenceItem, ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
from app.services.proposal_manual_flags import (
    apply_corpus_snippet_fills,
    apply_finalize_handoff_to_draft,
    apply_section_evidence_fills,
    build_presubmit_manual_fill_flags,
    gaps_to_manual_fill_flags,
)
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    asave_research_cache,
)
from app.services.proposal_retrieval_gap_fill import _merge_hits, _search_throttled
from app.services.proposal_retrieval_graph import EXCERPT_MAX_CHARS, _hit_excerpt, _hit_label
from app.services.proposal_rfp_compliance import (
    ComplianceGap,
    LedgerReconcileResult,
    reconcile_requirement_ledger,
    scan_rfp_compliance_gaps,
)

logger = logging.getLogger(__name__)

_GAP_EVIDENCE_QUERIES: dict[str, list[str]] = {
    "references": [
        "zö agency client references government contact name title phone email",
        "zö agency 06_WON 07_FIN reference letters client contacts",
    ],
    "questionnaire": [
        "zö agency FEIN EIN tax ID business phone email DUNS CAGE vendor questionnaire",
        "zö agency 02 master template business entity disclosure",
    ],
    "insurance": [
        "zö agency insurance ACORD certificate general liability professional liability limits",
        "zö agency Next Insurance workers compensation umbrella cyber",
    ],
    "workforce_data": [
        "zö agency workforce diversity EEO employee count minority female percentage",
        "zö agency HR employee demographics total employees",
    ],
    "budget": [
        "00_Guide_Pricing project management account management fee percentage agency",
        "00_Guide_Pricing labor category hourly rate card Low Average High",
    ],
    "psa_acknowledgment": [
        "zö agency insurance workers compensation compliance contract acknowledgments",
        "zö agency compliance certifications MacBride living wage",
    ],
    "requirement_coverage": [
        "zö agency certifications compliance forms insurance references",
    ],
    "submission_tag": [
        "zö agency vendor questionnaire FEIN insurance references compliance",
        "zö agency 02 master 06_WON 07_FIN team certifications",
    ],
    "budget_revenue": [
        "00_Guide_Pricing commission agency fee revenue line items",
        "zö agency 07_FIN budget pricing fee schedule",
    ],
}


async def _fetch_gap_evidence(
    gaps: list[ComplianceGap],
    *,
    corpus: list[EvidenceItem],
    rfp_client: str,
    rfp_sector: str,
) -> tuple[list[EvidenceItem], str, int]:
    categories = {g.category for g in gaps}
    queries: list[str] = []
    for category in categories:
        for template in _GAP_EVIDENCE_QUERIES.get(category, [])[:2]:
            queries.append(f"{template} {rfp_client} {rfp_sector}"[:240])

    for gap in gaps[:8]:
        snippet = (gap.excerpt or gap.rfp_requirement or gap.message)[:120].strip()
        if snippet:
            queries.append(f"zö agency {snippet} {rfp_client}"[:240])

    seen: set[str] = set()
    unique_queries: list[str] = []
    for query in queries:
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            unique_queries.append(query)

    section_id = gaps[0].section_id if gaps else "gap-finalize"
    updated = list(corpus)
    evidence_lines: list[str] = []
    hit_count = 0

    for query in unique_queries[:12]:
        hits = await _search_throttled(query)
        if hits:
            hit_count += len(hits)
            updated = _merge_hits(updated, hits, section_id)
            for hit in hits[:3]:
                excerpt = _hit_excerpt(hit, max_chars=EXCERPT_MAX_CHARS)
                label = _hit_label(hit)
                evidence_lines.append(f"- {label}: {excerpt[:800]}")

    block = "\n".join(evidence_lines[:24])
    return updated, block, hit_count


def _is_budget_section_id(section_id: str, draft: ProposalDraft) -> bool:
    idx = find_budget_section_index(draft.sections)
    return idx is not None and draft.sections[idx].id == section_id


async def _maybe_reconcile_budget_from_cache(
    rfp_id: str,
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    gaps: list[ComplianceGap],
) -> tuple[ProposalDraft, ProposalResearchCache | None, str | None]:
    """Budget gaps: re-render from cached budget object — no LLM."""
    has_budget_gap = any(g.category in ("budget", "budget_revenue") for g in gaps)
    if not has_budget_gap:
        return draft, research, None
    if not research or not research.budget:
        return draft, research, "budget:preserved — no cached budget object"

    from app.services.proposal_generator import run_phase3_5_budget_reconcile

    try:
        draft, research, _budget = await run_phase3_5_budget_reconcile(rfp_id)
    except Exception as exc:
        logger.warning("Gap finalize budget reconcile failed for %s: %s", rfp_id, exc)
        return draft, research, f"budget:preserved — reconcile failed ({exc})"
    return draft, research, "budget:reconciled from cached Supermemory budget"


def _apply_kb_fills_to_section(
    draft: ProposalDraft,
    section_id: str,
    corpus: list[EvidenceItem],
) -> tuple[ProposalDraft, int]:
    section = next((s for s in draft.sections if s.id == section_id), None)
    if not section:
        return draft, 0

    updated_content, fills = apply_section_evidence_fills(
        section_id,
        section.title,
        section.content or "",
        corpus,
    )
    if fills <= 0 or updated_content == (section.content or ""):
        return draft, 0

    now = datetime.now(timezone.utc).isoformat()
    sections = [
        s.model_copy(update={"content": updated_content}) if s.id == section_id else s
        for s in draft.sections
    ]
    return draft.model_copy(update={"sections": sections, "updated_at": now}), fills


async def _scrub_optional_verify_after_fills(
    rfp_id: str,
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    logs: list[str],
) -> tuple[ProposalDraft, list[str]]:
    """Drop remaining optional handoff tags / [VERIFY] the RFP does not require.

    Excludes the Budget/Pricing section explicitly — it is deterministically
    rendered from the canonical ProposalBudget object (render_budget_markdown)
    and must never go through a generic content-rewriting LLM pass, which has
    no awareness it must preserve an exact reconciled table. Today this call
    only avoided it by luck (the budget's own placeholders are "[MANUAL FILL:
    ...]"/"[PRICING FLAG: ...]", not literal "[VERIFY]"); make the exclusion
    explicit so a future guide/placeholder change can't silently reopen it.
    """
    try:
        from app.services.go_no_go_service import (
            _assess_rfp_content,
            combine_rfp_text,
        )
        from app.services.proposal_budget_content import find_budget_section_index
        from app.services.proposal_rfp_optional_claim_scrub import (
            apply_optional_claim_scrub_to_draft,
        )
        from app.services.proposal_verify_optional_scrub import (
            count_verify_tags,
            scrub_draft_optional_verify_tags,
        )

        content_info = _assess_rfp_content(rfp)
        rfp_text = combine_rfp_text(
            content_info.description or "",
            content_info.pdf_text or "",
        )
        budget_idx = find_budget_section_index(draft.sections)
        budget_section_id = draft.sections[budget_idx].id if budget_idx is not None else None
        skip_budget = {budget_section_id} if budget_section_id else set()

        draft, claim_logs = apply_optional_claim_scrub_to_draft(
            draft,
            rfp_text=rfp_text or "",
            skip_section_ids=skip_budget,
        )
        if claim_logs:
            logs.extend(claim_logs)
            await asave_proposal_draft(draft)

        verify_ids = {
            s.id
            for s in draft.sections
            if s.id != budget_section_id and count_verify_tags(s.content or "") > 0
        }
        scrubbed_sections, scrub_logs = await scrub_draft_optional_verify_tags(
            list(draft.sections),
            rfp_text=rfp_text or "",
            section_filter_ids=verify_ids,
        )
        if scrub_logs:
            logs.extend(scrub_logs)
        by_old = {s.id: (s.content or "") for s in draft.sections}
        changed = any(
            by_old.get(s.id, "") != (s.content or "") for s in scrubbed_sections
        )
        if changed:
            draft = draft.model_copy(
                update={
                    "sections": scrubbed_sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_proposal_draft(draft)
    except Exception:
        logger.exception(
            "Optional VERIFY scrub failed during finalize for %s (non-fatal)",
            rfp_id,
        )
    return draft, logs


async def run_submission_gap_finalize_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str], ProposalResearchCache | None]:
    """
    KB-only gap resolve: Supermemory search per flag cluster, deterministic VERIFY fills,
    budget reconcile when cached, optional VERIFY scrub, then MANUAL FILL handoff.
    No senior editor / surgical LLM pass.
    """
    if rfp is None:
        rfp, _, _ = await aload_rfp_for_proposal(rfp_id)
    draft = draft or await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for gap finalize pass.", status_code=400)
    research = research if research is not None else await aget_research_cache(rfp_id)

    logs: list[str] = []

    # Ledger reconcile runs first and unconditionally — it acts on
    # duplication and page-budget signals that scan_rfp_compliance_gaps below
    # never checks at all, so gating it behind "gaps" would skip it on a
    # draft with zero legacy compliance gaps but triplicated insurance
    # language. See proposal_rfp_compliance.reconcile_requirement_ledger.
    ledger_result = reconcile_requirement_ledger(draft=draft, research=research, rfp=rfp)
    if ledger_result.changed:
        draft = ledger_result.draft
        await asave_proposal_draft(draft)
    logs.extend(ledger_result.logs)
    if ledger_result.proposed_additions:
        preview = "; ".join(
            a.requirement_text[:80] for a in ledger_result.proposed_additions[:5]
        )
        logs.append(
            f"ledger:review-needed — {len(ledger_result.proposed_additions)} mandatory "
            f"requirement(s) have no matching section (not auto-added): {preview}"
        )

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        logger.info("Gap finalize for %s: no compliance gaps", rfp_id)
        draft, logs = await _scrub_optional_verify_after_fills(
            rfp_id, rfp=rfp, draft=draft, logs=logs
        )
        return draft, logs, research

    corpus = list(research.evidence_corpus) if research else []
    updated_corpus, _, global_hits = await _fetch_gap_evidence(
        gaps,
        corpus=corpus,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector or "",
    )
    if global_hits:
        logs.append(f"kb:global — {global_hits} Supermemory hit(s)")
    if research and updated_corpus != corpus:
        research = research.model_copy(
            update={
                "evidence_corpus": updated_corpus,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        await asave_research_cache(research)

    draft = apply_corpus_snippet_fills(draft, updated_corpus)
    await asave_proposal_draft(draft)

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        logger.info("Gap finalize for %s: corpus fills cleared all gaps", rfp_id)
        draft, logs = await _scrub_optional_verify_after_fills(
            rfp_id, rfp=rfp, draft=draft, logs=logs
        )
        return draft, logs + ["corpus-fills:all gaps cleared"], research

    draft, research, budget_log = await _maybe_reconcile_budget_from_cache(
        rfp_id,
        draft=draft,
        research=research,
        gaps=gaps,
    )
    if budget_log:
        logs.append(budget_log)
        gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
        if not gaps:
            logger.info("Gap finalize for %s: budget reconcile cleared all gaps", rfp_id)
            draft, logs = await _scrub_optional_verify_after_fills(
                rfp_id, rfp=rfp, draft=draft, logs=logs
            )
            return draft, logs, research

    by_section: dict[str, list[ComplianceGap]] = {}
    for gap in gaps:
        by_section.setdefault(gap.section_id, []).append(gap)

    logger.info(
        "Gap finalize for %s: KB-only pass — %d gap(s) in %d section(s)",
        rfp_id,
        len(gaps),
        len(by_section),
    )

    for section_id, section_gaps in by_section.items():
        if _is_budget_section_id(section_id, draft):
            logs.append(f"finalize:{section_id}: budget section preserved (reconcile only)")
            continue

        section_corpus, _evidence_block, section_hits = await _fetch_gap_evidence(
            section_gaps,
            corpus=updated_corpus,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector or "",
        )
        if research and section_corpus != updated_corpus:
            updated_corpus = section_corpus
            research = research.model_copy(
                update={
                    "evidence_corpus": updated_corpus,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_research_cache(research)

        draft, fill_count = _apply_kb_fills_to_section(draft, section_id, updated_corpus)
        if fill_count > 0:
            await asave_proposal_draft(draft)
            logs.append(
                f"finalize:{section_id}: kb-fill — {fill_count} tag(s) from {section_hits} hit(s)"
            )
        else:
            logs.append(
                f"finalize:{section_id}: kb-query — {section_hits} hit(s), no deterministic fill"
            )

    draft, logs = await _scrub_optional_verify_after_fills(
        rfp_id, rfp=rfp, draft=draft, logs=logs
    )

    remaining = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    draft = apply_finalize_handoff_to_draft(draft, remaining)
    await asave_proposal_draft(draft)

    remaining = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if remaining:
        flag_preview = gaps_to_manual_fill_flags(remaining, kb_searched=True, finalized=True)
        logger.warning(
            "Gap finalize for %s: %d gap(s) remain → %d MANUAL FILL flag(s) for Sonja/Ella",
            rfp_id,
            len(remaining),
            len(flag_preview),
        )
        for gf in flag_preview[:5]:
            logs.append(f"manual-fill:{gf.section_id}: {gf.tag[:100]}")
    else:
        logger.info("Gap finalize for %s: all compliance gaps cleared", rfp_id)

    await asave_proposal_draft(draft)
    return draft, logs, research


async def run_requirement_ledger_reconcile_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
    rfp_text: str | None = None,
) -> LedgerReconcileResult:
    """Standalone entry point for the Task 5 reconciler.

    ``run_submission_gap_finalize_pass`` already calls this internally on
    every invocation, so callers that only need the KB-fill/VERIFY-scrub
    pipeline don't need this. This exists for a caller that wants the
    structured result on its own — e.g. a future "review proposed additions"
    panel — where ``LedgerReconcileResult.proposed_additions`` (never
    applied) must stay visibly distinct from ``applied_merges`` /
    ``applied_cuts`` (already saved to the draft).
    """
    if rfp is None:
        rfp, _, _ = await aload_rfp_for_proposal(rfp_id)
    draft = draft or await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError(
            "No proposal draft for requirement ledger reconcile.", status_code=400
        )
    research = research if research is not None else await aget_research_cache(rfp_id)

    result = reconcile_requirement_ledger(
        draft=draft, research=research, rfp=rfp, rfp_text=rfp_text
    )
    if result.changed:
        await asave_proposal_draft(result.draft)
    return result


def attach_manual_fill_flags_to_review(
    review: "PreSubmitReview",
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    kb_searched: bool = True,
    finalized: bool = True,
) -> "PreSubmitReview":
    """Add manualFillFlags to an existing PreSubmitReview."""
    from app.models.proposal import PreSubmitReview
    from app.services.proposal_manual_flags import summarize_manual_fill_flags

    flags = build_presubmit_manual_fill_flags(
        draft=draft,
        research=research,
        rfp=rfp,
        kb_searched=kb_searched,
        finalized=finalized,
    )
    summary = review.summary
    if flags:
        flag_summary = summarize_manual_fill_flags(flags)
        if finalized:
            summary = (
                f"{summary} Manual handoff: {flag_summary}"
                if summary
                else f"Manual handoff: {flag_summary}"
            )
    return review.model_copy(
        update={"manual_fill_flags": flags, "summary": summary}
    )
