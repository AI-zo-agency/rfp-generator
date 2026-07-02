"""Final editor pass — Supermemory gap-fill then owner-assigned MANUAL FILL flags."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.models.proposal import EvidenceItem, ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
from app.services.proposal_manual_flags import (
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
    build_rfp_compliance_repair_brief,
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
        "zö agency 07_FIN burdened hourly rates fee schedule",
    ],
    "psa_acknowledgment": [
        "zö agency insurance workers compensation compliance contract acknowledgments",
        "zö agency compliance certifications MacBride living wage",
    ],
    "requirement_coverage": [
        "zö agency certifications compliance forms insurance references",
    ],
}

_FINALIZE_REPAIR_RULES = """
FINAL SUBMISSION GAP PASS — last editor before manual handoff to Sonja/Ella.

Rules (strict):
1. Use ONLY facts from the gap-fill evidence below or KB search tools. Cite as [E#] when used.
2. Fill every field you can from evidence (reference contacts, FEIN, phones, insurance limits, workforce %).
3. For any required field STILL missing after KB search, replace vague tags with exactly ONE tag per gap:
   [MANUAL FILL: Sonja — specific field and action]
   [MANUAL FILL: Ella — specific field and decision needed]
   Examples:
   - [MANUAL FILL: Sonja — confirm E&O umbrella binder with Next Insurance before July 8]
   - [MANUAL FILL: Sonja — business phone and primary email on vendor questionnaire]
   - [MANUAL FILL: Ella — NJ college reference go/no-go; use University of Idaho if proceeding]
4. NEVER leave [PLACEHOLDER], bare [VERIFY], TBD, ___, or "upon request" for required submission data.
5. Do NOT invent reference contacts, insurance binders, or tax IDs — evidence or MANUAL FILL only.
6. Insurance gaps: keep limits table; mark unconfirmed cells with MANUAL FILL for Sonja, not silent blanks.
7. Preserve strong narrative and compliance form structure; surgical edits only where gaps exist.
"""


async def _fetch_gap_evidence(
    gaps: list[ComplianceGap],
    *,
    corpus: list[EvidenceItem],
    rfp_client: str,
    rfp_sector: str,
) -> tuple[list[EvidenceItem], str]:
    categories = {g.category for g in gaps}
    queries: list[str] = []
    for category in categories:
        for template in _GAP_EVIDENCE_QUERIES.get(category, [])[:2]:
            queries.append(f"{template} {rfp_client} {rfp_sector}"[:240])

    for gap in gaps[:6]:
        snippet = (gap.rfp_requirement or gap.message)[:100].strip()
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

    for query in unique_queries[:10]:
        hits = await _search_throttled(query)
        if hits:
            updated = _merge_hits(updated, hits, section_id)
            for hit in hits[:3]:
                excerpt = _hit_excerpt(hit, max_chars=EXCERPT_MAX_CHARS)
                label = _hit_label(hit)
                evidence_lines.append(f"- {label}: {excerpt[:800]}")

    block = "\n".join(evidence_lines[:20])
    return updated, block


def _build_finalize_brief(
    section_gaps: list[ComplianceGap],
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    gap_evidence_block: str,
) -> str:
    base = build_rfp_compliance_repair_brief(
        section_gaps,
        draft=draft,
        research=research,
        rfp=rfp,
    )
    parts = [
        base,
        "",
        _FINALIZE_REPAIR_RULES.strip(),
    ]
    if gap_evidence_block.strip():
        parts.extend(["", "## Supermemory gap-fill evidence (use first)", gap_evidence_block])
    else:
        parts.extend([
            "",
            "## Supermemory gap-fill evidence",
            "(No new KB hits — search tools, then assign [MANUAL FILL: owner — field] for each gap.)",
        ])
    return "\n".join(parts)


async def run_submission_gap_finalize_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str], ProposalResearchCache | None]:
    """
    Last editor: targeted Supermemory retrieval + section repair per gap cluster.
    Remaining gaps become finalized MANUAL FILL flags on the next presubmit review.
    """
    from app.services.proposal_manual_flags import (
        apply_corpus_snippet_fills,
        apply_finalize_handoff_to_draft,
    )
    from app.services.proposal_self_edit_loop import _repair_one_section

    if rfp is None:
        rfp, _, _ = await aload_rfp_for_proposal(rfp_id)
    draft = draft or await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for gap finalize pass.", status_code=400)
    research = research if research is not None else await aget_research_cache(rfp_id)
    budget = research.budget if research else None

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        logger.info("Gap finalize for %s: no compliance gaps", rfp_id)
        return draft, [], research

    corpus = list(research.evidence_corpus) if research else []
    updated_corpus, _ = await _fetch_gap_evidence(
        gaps,
        corpus=corpus,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector or "",
    )
    if research and updated_corpus != corpus:
        now = datetime.now(timezone.utc).isoformat()
        research = research.model_copy(
            update={"evidence_corpus": updated_corpus, "updated_at": now}
        )
        await asave_research_cache(research)

    draft = apply_corpus_snippet_fills(draft, updated_corpus)
    await asave_proposal_draft(draft)

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        logger.info("Gap finalize for %s: corpus fills cleared all gaps", rfp_id)
        return draft, ["corpus-fills:all gaps cleared"], research

    by_section: dict[str, list[ComplianceGap]] = {}
    for gap in gaps:
        by_section.setdefault(gap.section_id, []).append(gap)

    logs: list[str] = []
    rfp_client = rfp.client
    rfp_title = rfp.title

    logger.info(
        "Gap finalize for %s: %d gap(s) in %d section(s)",
        rfp_id,
        len(gaps),
        len(by_section),
    )

    for section_id, section_gaps in by_section.items():
        section_corpus, evidence_block = await _fetch_gap_evidence(
            section_gaps,
            corpus=updated_corpus,
            rfp_client=rfp_client,
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

        brief = _build_finalize_brief(
            section_gaps,
            draft=draft,
            research=research,
            rfp=rfp,
            gap_evidence_block=evidence_block,
        )
        sid, improved, detail = await _repair_one_section(
            rfp_id,
            section_id,
            use_senior_editor=False,
            rfp=rfp,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
            budget=budget,
            repair_message=brief,
        )
        log_line = f"finalize:{sid}: {'improved' if improved else 'unchanged'} — {detail[:100]}"
        logs.append(log_line)
        logger.info("Gap finalize section %s: %s", sid, detail[:120])
        draft = await aget_proposal_draft(rfp_id) or draft

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
