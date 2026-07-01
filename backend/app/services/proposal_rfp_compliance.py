"""RFP-to-proposal compliance scanning — requirement gaps, not client-specific static fixes."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import find_budget_section_index

logger = logging.getLogger(__name__)

_DEFER_RE = re.compile(
    r"\b(?:upon request|on request|will be provided|provided in attachment|"
    r"supplemental attachment|on file with|attachment\s*\d+|"
    r"available upon|furnished upon|provided separately)\b",
    re.I,
)
_PHONE_RE = re.compile(
    r"(?:\(\d{3}\)\s*\d{3}[-.\s]?\d{4}|\d{3}[-.\s]\d{3}[-.\s]\d{4}|"
    r"phone[:\s]+\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})",
    re.I,
)
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%", re.I)
_HOURS_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:hours|hrs)\b|\bhours\s*(?:per|by)\b|staff\s+hours",
    re.I,
)
_HEADCOUNT_RE = re.compile(
    r"\b\d+\s+(?:total\s+)?employees?\b|\bheadcount\b|\btotal\s+employees?\s*[:=]\s*\d+",
    re.I,
)

_RFP_REFERENCES_RE = re.compile(
    r"\breference\b.*\b(?:contact|phone|telephone)\b|\bcontact\s+names?\b.*\bphone\b",
    re.I,
)
_RFP_DIVERSITY_RE = re.compile(
    r"\b(?:divers\w*|minorit\w*|female|workforce).{0,40}\b(?:percent|%|number of employees)\b|"
    r"\btotal\s+number\s+of\s+employees\b",
    re.I,
)
_RFP_HOURS_RE = re.compile(
    r"\bstaff\s+hours\b|\bhours\s+and\s+billing\s+rates\b|\bitemized\s+budget\b.*\bhours\b",
    re.I,
)

_PSA_ACKNOWLEDGMENTS: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    (
        "MacBride Principles",
        re.compile(r"\bmacbride\s+principles?\b", re.I),
        re.compile(r"\bmacbride\b", re.I),
    ),
    (
        "Workers' Compensation / Disability Benefits",
        re.compile(r"\bworkers['\u2019\s]*compensation\b|\bdisability\s+benefits?\b", re.I),
        re.compile(r"\bworkers['\u2019\s]*compensation\b|\bdisability\s+benefit", re.I),
    ),
    (
        "Living Wage",
        re.compile(r"\bliving\s+wage\b", re.I),
        re.compile(r"\bliving\s+wage\b", re.I),
    ),
    (
        "Title VI / Civil Rights",
        re.compile(r"\btitle\s*vi\b|\bcivil\s+rights\s+act\b", re.I),
        re.compile(r"\btitle\s*vi\b|\bcivil\s+rights\b", re.I),
    ),
    (
        "Criminal history / Chapter 63",
        re.compile(r"\bchapter\s*63\b|criminal\s+history\s+inquir", re.I),
        re.compile(r"\bchapter\s*63\b|ban[\s-]*the[\s-]*box|criminal\s+history", re.I),
    ),
    (
        "Independent contractor status",
        re.compile(r"\bindependent\s+contractor\b", re.I),
        re.compile(r"\bindependent\s+contractor\b", re.I),
    ),
    (
        "Audit rights",
        re.compile(r"\baudit\s+rights?\b|\brecords?.{0,30}audit\b", re.I),
        re.compile(r"\baudit\s+rights?\b|\b(?:three|3)[\s-]*year.{0,20}audit\b", re.I),
    ),
    (
        "NY-admitted insurance carrier",
        re.compile(r"\bnew\s+york[\s-]*admitted\b|\bny[\s-]*admitted\b", re.I),
        re.compile(r"\bnew\s+york[\s-]*admitted\b|\bny[\s-]*admitted\b", re.I),
    ),
    (
        "FOIL compliance",
        re.compile(r"\bfreedom\s+of\s+information\b|\bfoil\b", re.I),
        re.compile(r"\bfreedom\s+of\s+information\b|\bfoil\b", re.I),
    ),
]


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


def _rfp_blob(rfp: RfpRecord, research: ProposalResearchCache | None) -> str:
    parts: list[str] = []
    if rfp.title:
        parts.append(rfp.title)
    if rfp.client:
        parts.append(rfp.client)
    if research:
        for section in research.rfp_sections:
            parts.append(section.title or "")
            parts.extend(section.requirements or [])
    return "\n".join(parts)


def _section_by_title_patterns(
    draft: ProposalDraft,
    *patterns: str,
) -> ProposalSection | None:
    for section in draft.sections:
        title = (section.title or "").casefold()
        if any(p in title for p in patterns):
            return section
    return None


def _best_section_for_psa_ack(draft: ProposalDraft) -> ProposalSection | None:
    for patterns in (
        ("qualification",),
        ("project statement", "company overview", "section 1"),
        ("mwbe", "workforce"),
        ("methodology", "description of services"),
    ):
        match = _section_by_title_patterns(draft, *patterns)
        if match:
            return match
    for section in draft.sections:
        if section.content.strip():
            return section
    return None


def scan_reference_contact_gaps(
    *,
    draft: ProposalDraft,
    rfp_blob: str,
) -> list[ComplianceGap]:
    if not _RFP_REFERENCES_RE.search(rfp_blob):
        return []

    section = _section_by_title_patterns(
        draft, "qualification", "reference", "past performance", "experience"
    )
    if not section or not section.content.strip():
        return []

    content = section.content
    has_phone = bool(_PHONE_RE.search(content))
    defers = bool(_DEFER_RE.search(content))

    if has_phone and not defers:
        return []

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="references",
            message=(
                "RFP requires reference contact names and phone numbers in the proposal — "
                "do not defer to unnamed attachments or 'upon request'"
            ),
            rfp_requirement="references, contact names, and phone numbers",
            excerpt=content[:280],
            repair_hint=(
                "Search KB (06_WON, references, case studies) for each reference client's "
                "contact name, title, phone, and email. Include in prose or a table. "
                "Use [VERIFY: contact name/phone from HR or project files] only if KB has no data."
            ),
        )
    ]


def scan_workforce_diversity_gaps(
    *,
    draft: ProposalDraft,
    rfp_blob: str,
) -> list[ComplianceGap]:
    if not _RFP_DIVERSITY_RE.search(rfp_blob):
        return []

    section = _section_by_title_patterns(
        draft, "personnel", "team", "staff", "workforce", "section 2"
    )
    if not section or not section.content.strip():
        return []

    content = section.content
    has_percent = bool(_PERCENT_RE.search(content))
    has_headcount = bool(_HEADCOUNT_RE.search(content))
    defers = bool(_DEFER_RE.search(content))

    if has_percent and has_headcount and not defers:
        return []

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="workforce_data",
            message=(
                "RFP requires workforce diversity data (headcount, % minority, % female) "
                "in the proposal body — not 'upon request' or supplemental attachment"
            ),
            rfp_requirement="workforce diversity including total employees and percentages",
            excerpt=content[:280],
            repair_hint=(
                "Search KB for zö agency employee count, EEO, minority and female percentages. "
                "State actual numbers in a short table. If HR data missing, insert "
                "[VERIFY: Ella to confirm current headcount and M/W/minority % from HR records]."
            ),
        )
    ]


def scan_budget_hours_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_blob: str,
) -> list[ComplianceGap]:
    if not _RFP_HOURS_RE.search(rfp_blob):
        return []

    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return []
    section = draft.sections[idx]
    if not section.content.strip():
        return []

    content = section.content
    has_hours = bool(_HOURS_RE.search(content))
    commission = bool(
        research
        and research.budget
        and (
            (research.budget.commission_model or "")
            or (research.budget.client_media_passthrough or 0) > 0
        )
    )
    explains_commission = bool(
        re.search(r"\bcommission\b.*\b(?:model|structure|psa|total compensation)\b", content, re.I)
    )

    if has_hours or (commission and explains_commission):
        return []

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="budget_hours",
            message=(
                "RFP requires staff hours and billing rates per scope task — add an hours "
                "table OR explain commission-model compensation with optional transparency table"
            ),
            rfp_requirement="itemized budget including staff hours and billing rates",
            excerpt=content[:280],
            repair_hint=(
                "If commission-model: state that PSA/contract commission is total compensation, "
                "then add estimated hours-by-task table for transparency (from scope line items). "
                "If lump-sum/hourly: add hours × rate table from budget line items."
            ),
        )
    ]


def scan_psa_acknowledgment_gaps(
    *,
    draft: ProposalDraft,
    rfp_blob: str,
) -> list[ComplianceGap]:
    gaps: list[ComplianceGap] = []
    manuscript = _manuscript_blob(draft)
    target = _best_section_for_psa_ack(draft)
    if not target:
        return gaps

    for label, rfp_pat, proposal_pat in _PSA_ACKNOWLEDGMENTS:
        if not rfp_pat.search(rfp_blob):
            continue
        if proposal_pat.search(manuscript):
            continue
        gaps.append(
            ComplianceGap(
                section_id=target.id,
                section_title=target.title,
                category="psa_acknowledgment",
                message=f"PSA/RFP requires acknowledgment of {label} — not stated in proposal",
                rfp_requirement=label,
                excerpt=(target.content or "")[:200],
                repair_hint=(
                    f"Add a concise compliance sentence acknowledging {label} per the RFP/PSA. "
                    "Search KB for insurance certs, registrations, or policy facts — do not invent. "
                    "Use [VERIFY: confirm with Sonja/Ella] only for facts not in KB."
                ),
            )
        )
    return gaps


def scan_rfp_compliance_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[ComplianceGap]:
    """Requirement-level gaps: deferred data, missing PSA acks, hours, diversity."""
    blob = _rfp_blob(rfp, research)
    gaps: list[ComplianceGap] = []
    gaps.extend(scan_reference_contact_gaps(draft=draft, rfp_blob=blob))
    gaps.extend(scan_workforce_diversity_gaps(draft=draft, rfp_blob=blob))
    gaps.extend(scan_budget_hours_gaps(draft=draft, research=research, rfp_blob=blob))
    gaps.extend(scan_psa_acknowledgment_gaps(draft=draft, rfp_blob=blob))
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
        "3. If KB lacks a fact after search, use [VERIFY: specific field needed] — not a soft deferral.",
        "4. Preserve strong narrative; add compliance sentences/tables where gaps exist.",
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
    """LLM repair for RFP requirement gaps (references, hours, diversity, PSA acks)."""
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
