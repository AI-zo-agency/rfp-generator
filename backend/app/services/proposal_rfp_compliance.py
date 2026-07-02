"""RFP-to-proposal compliance scanning — driven by Phase 2 research, not static regex."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_budget_validation import (
    derive_commission_agency_revenue,
    is_commission_style_budget,
)

logger = logging.getLogger(__name__)

OPEN_TAG_MARKERS = ("[VERIFY", "[PLACEHOLDER", "[TBD", "[INSERT")
MANUAL_FILL_MARKER = "[MANUAL FILL"

_REQ_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "shall",
        "must",
        "will",
        "have",
        "been",
        "provide",
        "include",
        "submit",
        "proposal",
        "offeror",
        "vendor",
        "contractor",
        "services",
        "required",
        "agency",
    }
)


@dataclass(frozen=True)
class ComplianceGap:
    section_id: str
    section_title: str
    category: str
    message: str
    rfp_requirement: str
    excerpt: str
    repair_hint: str


def _manuscript_blob(draft: ProposalDraft) -> str:
    return "\n\n".join(
        f"## {s.title}\n{s.content}" for s in draft.sections if (s.content or "").strip()
    )


def _section_by_title_patterns(
    draft: ProposalDraft,
    *patterns: str,
) -> ProposalSection | None:
    for section in draft.sections:
        title = (section.title or "").casefold()
        if any(p in title for p in patterns):
            return section
    return None


def _words_from_title(title: str) -> list[str]:
    words: list[str] = []
    normalized = (
        title.casefold()
        .replace("-", " ")
        .replace("—", " ")
        .replace("/", " ")
    )
    for word in normalized.split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if len(cleaned) > 4:
            words.append(cleaned)
    return words


def _section_for_mapped_title(
    draft: ProposalDraft,
    mapped_title: str,
) -> ProposalSection | None:
    title_key = (mapped_title or "").strip().casefold()
    by_title = {(s.title or "").strip().casefold(): s for s in draft.sections}
    if title_key in by_title:
        return by_title[title_key]

    words = _words_from_title(mapped_title or "")
    if not words:
        return None
    for section in draft.sections:
        section_title = (section.title or "").casefold()
        if any(word in section_title for word in words):
            return section
    return None


def _text_contains(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def _has_manual_fill_handoff(content: str, *keywords: str) -> bool:
    lower = content.casefold()
    start = 0
    while True:
        idx = lower.find(MANUAL_FILL_MARKER.casefold(), start)
        if idx < 0:
            return False
        end = lower.find("]", idx)
        if end < 0:
            return False
        tag = lower[idx : end + 1]
        if any(keyword.casefold() in tag for keyword in keywords):
            return True
        start = end + 1


def _unresolved_submission_placeholders(content: str) -> bool:
    upper = content.upper()
    if any(marker in upper for marker in OPEN_TAG_MARKERS):
        return True
    padded = f" {content} "
    return " TBD " in padded.upper() or "___" in content


def _requirement_tokens(req: str) -> list[str]:
    tokens: list[str] = []
    for word in req.casefold().split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if len(cleaned) >= 5 and cleaned not in _REQ_STOPWORDS:
            tokens.append(cleaned)
    return tokens[:8]


def requirement_likely_covered(req: str, manuscript: str) -> bool:
    """Heuristic: enough requirement keywords appear in manuscript prose."""
    tokens = _requirement_tokens(req)
    if not tokens:
        return True
    manuscript_cf = manuscript.casefold()
    hits = sum(1 for token in tokens if token in manuscript_cf)
    return hits >= max(2, (len(tokens) + 1) // 2)


def scan_open_submission_tags(*, draft: ProposalDraft) -> list[ComplianceGap]:
    """Flag sections that still contain open VERIFY / PLACEHOLDER / TBD tags."""
    gaps: list[ComplianceGap] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip() or not _unresolved_submission_placeholders(content):
            continue
        gaps.append(
            ComplianceGap(
                section_id=section.id,
                section_title=section.title,
                category="submission_tag",
                message=(
                    "Section still contains open submission placeholders "
                    "(VERIFY, PLACEHOLDER, TBD, or INSERT) — fill from KB or assign MANUAL FILL"
                ),
                rfp_requirement="submission-ready prose with no open placeholder tags",
                excerpt=content[:280],
                repair_hint=(
                    "Search KB for the missing fact. If KB cannot supply it, replace with exactly one "
                    "[MANUAL FILL: Sonja — field] or [MANUAL FILL: Ella — field] tag per gap."
                ),
            )
        )
    return gaps


def scan_uncovered_requirement_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[ComplianceGap]:
    """Gaps from Phase 2 mapped sections — dynamic per RFP, not static patterns."""
    if not research or not research.rfp_sections:
        return []

    manuscript = _manuscript_blob(draft)
    gaps: list[ComplianceGap] = []

    for mapped in research.rfp_sections:
        uncovered = mapped.uncovered_requirements or []
        if not uncovered:
            continue

        section = _section_for_mapped_title(draft, mapped.title or "")
        if not section:
            continue

        content = section.content or ""
        for req in uncovered[:5]:
            if _has_manual_fill_handoff(content, *(_requirement_tokens(req)[:3])):
                continue
            if requirement_likely_covered(req, content) or requirement_likely_covered(req, manuscript):
                continue
            gaps.append(
                ComplianceGap(
                    section_id=section.id,
                    section_title=section.title,
                    category="requirement_coverage",
                    message=(
                        "Phase 2 research flagged an uncovered RFP requirement still missing "
                        f"from the manuscript: {req[:120]}"
                    ),
                    rfp_requirement=req[:200],
                    excerpt=content[:240],
                    repair_hint=(
                        "Address this requirement explicitly in prose, a compliance table, or form "
                        "response. Search KB for supporting facts; use MANUAL FILL only after KB "
                        "search returns nothing."
                    ),
                )
            )

    return gaps[:15]


def scan_budget_revenue_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[ComplianceGap]:
    """Budget math gaps from canonical budget fields — no prose regex."""
    budget = research.budget if research else None
    if not budget:
        return []

    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return []
    section = draft.sections[idx]

    revenue = float(budget.agency_revenue_estimate or 0)
    derived = derive_commission_agency_revenue(budget) or 0.0
    if revenue > 0 or (derived > 0 and abs(revenue - derived) < 1.0):
        return []

    commission_style = is_commission_style_budget(budget)
    lump = float(budget.lump_sum_total or 0)
    if not commission_style and lump <= 0:
        return []

    if commission_style and revenue <= 0:
        message = (
            "Budget Summary shows $0 agency revenue for a commission-model RFP — "
            "set agencyRevenueEstimate from commission rate × pass-through (or line items)"
        )
    else:
        message = (
            "Budget Summary shows $0 agency revenue — reconcile agencyRevenueEstimate "
            "with budget line items and commission structure"
        )

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="budget_revenue",
            message=message,
            rfp_requirement="itemized budget with correct agency fee / commission totals",
            excerpt=(section.content or "")[:200],
            repair_hint=(
                "Run Budget refinery or reconcile: agencyRevenueEstimate must match "
                "commission structure and line-item subtotals."
            ),
        )
    ]


def scan_submission_pricing_flag_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[ComplianceGap]:
    """Internal pricing flags on budget object or budget section text."""
    budget = research.budget if research else None
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return []

    section = draft.sections[idx]
    content = section.content or ""
    has_manuscript_flag = _text_contains(content, "[PRICING FLAG") or _text_contains(
        content, "## Pricing Flags"
    )
    has_budget_flags = bool(budget and budget.pricing_flags)

    if not has_manuscript_flag and not has_budget_flags:
        return []

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="budget",
            message=(
                "Budget still has internal pricing flags — resolve fee decisions with Sonja, "
                "then regenerate or reconcile budget before submission"
            ),
            rfp_requirement="clean cost proposal without internal review notes",
            excerpt=content[:280],
            repair_hint=(
                "Apply scope-adjustment rates to line items, clear pricing_flags in budget "
                "refinery, and re-sync the budget section."
            ),
        )
    ]


def scan_rfp_compliance_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[ComplianceGap]:
    """Dynamic compliance gaps: Phase 2 uncovered reqs, open tags, canonical budget."""
    _ = rfp  # reserved for future RFP-record fields; gaps come from research + draft
    gaps: list[ComplianceGap] = []
    gaps.extend(scan_open_submission_tags(draft=draft))
    gaps.extend(scan_uncovered_requirement_gaps(draft=draft, research=research))
    gaps.extend(scan_budget_revenue_gaps(draft=draft, research=research))
    gaps.extend(scan_submission_pricing_flag_gaps(draft=draft, research=research))
    return gaps


def compliance_gaps_for_section(
    gaps: list[ComplianceGap],
    section_id: str,
) -> list[ComplianceGap]:
    return [g for g in gaps if g.section_id == section_id]


def sections_with_compliance_gaps(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> set[str]:
    return {g.section_id for g in scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)}


def build_rfp_compliance_repair_brief(
    gaps: list[ComplianceGap],
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> str:
    lines = [
        "RFP COMPLIANCE REPAIR — fill required submission data; do not defer to unnamed attachments.",
        f"Client: {rfp.client} | RFP: {rfp.title}",
        "",
        "Rules:",
        "1. Search KB tools (references, team bios, certifications, 06_WON, 07_FIN, insurance) for facts.",
        "2. NEVER write 'upon request', 'Attachment 05', or 'will be provided separately' for required data.",
        "3. NEVER show $0 for agency revenue / commission when fees apply — use commissionRate × pass-through or agency_fee line items.",
        "4. If KB lacks a fact after search, use [MANUAL FILL: owner — specific field] — not bare VERIFY.",
        "5. Preserve strong narrative; add compliance sentences/tables where gaps exist.",
        "6. Address each uncovered requirement from the mapped RFP section list below.",
        "",
        "## Compliance gaps to fix",
    ]
    for index, gap in enumerate(gaps, start=1):
        lines.append(f"{index}. **[{gap.category}]** {gap.message}")
        lines.append(f"   RFP requires: {gap.rfp_requirement}")
        lines.append(f"   Repair: {gap.repair_hint}")
        if gap.excerpt:
            lines.append(f'   Current excerpt: "{gap.excerpt[:200]}…"')

    mapped = research.rfp_sections if research else []
    if mapped:
        lines.extend(["", "## Mapped RFP section requirements (context)"])
        for m in mapped[:12]:
            reqs = (m.requirements or [])[:4]
            if reqs:
                lines.append(f"- **{m.title}:**")
                for req in reqs:
                    lines.append(f"  - {req[:160]}")

    return "\n".join(lines)


def compliance_gaps_to_presubmit_issues(
    gaps: list[ComplianceGap],
) -> list:
    from app.models.proposal import PreSubmitIssue

    return [
        PreSubmitIssue(
            severity="critical",
            category="compliance",
            message=gap.message,
            sectionId=gap.section_id,
            sectionTitle=gap.section_title,
            excerpt=gap.excerpt[:200] if gap.excerpt else None,
        )
        for gap in gaps
    ]


async def run_rfp_compliance_polish_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """LLM repair for RFP requirement gaps surfaced by Phase 2 research."""
    from app.services.proposal_common import ProposalError, load_rfp_for_proposal
    from app.services.proposal_repository import get_proposal_draft, get_research_cache
    from app.services.proposal_self_edit_loop import _repair_one_section

    if rfp is None:
        rfp, _, _ = load_rfp_for_proposal(rfp_id)
    draft = draft or get_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for RFP compliance polish.", status_code=400)
    research = research if research is not None else get_research_cache(rfp_id)
    budget = research.budget if research else None

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        return draft, []

    by_section: dict[str, list[ComplianceGap]] = {}
    for gap in gaps:
        by_section.setdefault(gap.section_id, []).append(gap)

    logs: list[str] = []
    rfp_client = rfp.client
    rfp_title = rfp.title

    for section_id, section_gaps in by_section.items():
        brief = build_rfp_compliance_repair_brief(
            section_gaps,
            draft=draft,
            research=research,
            rfp=rfp,
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
        log_line = f"{sid}: {'fixed' if improved else 'unchanged'} — {detail[:120]}"
        logs.append(log_line)
        draft = get_proposal_draft(rfp_id) or draft

    remaining = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if remaining:
        logger.warning(
            "RFP compliance polish for %s: %d gap(s) remain",
            rfp_id,
            len(remaining),
        )

    return draft, logs
