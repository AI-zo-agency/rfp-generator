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
from app.services.proposal_rfp_compliance import ComplianceGap, scan_rfp_compliance_gaps

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
        "zö agency 07_FIN burdened hourly rates fee schedule",
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


async def run_submission_gap_finalize_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str], ProposalResearchCache | None]:
    """
    KB-only gap resolve: Supermemory search per flag cluster, deterministic VERIFY fills,
    budget reconcile when cached, then MANUAL FILL handoff for anything still open.
    No senior editor / surgical LLM pass.
    """
    if rfp is None:
        rfp, _, _ = await aload_rfp_for_proposal(rfp_id)
    draft = draft or await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for gap finalize pass.", status_code=400)
    research = research if research is not None else await aget_research_cache(rfp_id)

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        logger.info("Gap finalize for %s: no compliance gaps", rfp_id)
        return draft, [], research

    logs: list[str] = []
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
