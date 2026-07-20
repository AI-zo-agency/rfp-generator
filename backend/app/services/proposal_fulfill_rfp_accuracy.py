"""Fulfill pass: KPI scope, cost scoring, and budget container vs THIS RFP text."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_rfp_excerpt import evaluation_and_kpi_excerpt

logger = logging.getLogger(__name__)

_COST_CEILING_AS_WIN_RE = re.compile(
    r"(?:highest|best|maximum|full).{0,40}(?:cost|price).{0,40}(?:rating|score|points)|"
    r"(?:at|matching).{0,30}ceiling.{0,80}(?:highest|best|maximum)|"
    r"competitive.{0,40}ceiling.{0,40}(?:rating|score)",
    re.I | re.S,
)

_AGENCY_KPI_CUES = (
    "strategic plan",
    "agency is responsible",
    "agency-wide",
    "organizational scorecard",
    "four key performance indicators that the agency",
)

_CONTRACTOR_KPI_CUE_RE = re.compile(
    r"contractor.{0,120}responsible.{0,80}key performance indicator|"
    r"for this rfp.{0,120}identified.{0,40}(\d+|three|two|four).{0,40}key performance",
    re.I | re.S,
)

_EXCEL_ATTACHMENT_RE = re.compile(
    r"attachment\s+0?1\b|excel.{0,60}(?:worksheet|workbook|template)|"
    r"budget.{0,80}separate.{0,40}(?:attachment|excel|worksheet)",
    re.I | re.S,
)

_INVERSE_COST_SCORING_RE = re.compile(
    r"lowest.{0,80}cost factor|lowest proposal price multiplied|"
    r"maximum points available for price.{0,80}divided by the higher",
    re.I | re.S,
)


@dataclass
class RfpScoringFacts:
    contractor_kpi_count: int | None = None
    contractor_kpi_summary: str = ""
    agency_kpi_note: str = ""
    cost_scoring_inverse: bool = False
    cost_scoring_summary: str = ""
    cost_points_criteria_4: int | None = None
    cost_points_criteria_5: int | None = None
    cost_points_combined: int | None = None
    budget_submission_format: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccuracyFinding:
    kind: str
    message: str
    section_ids: list[str] = field(default_factory=list)


def _section_blob(section: ProposalSection) -> str:
    return f"{section.title}\n{section.content or ''}"


def _kpi_related_sections(draft: ProposalDraft) -> list[ProposalSection]:
    needles = (
        "kpi",
        "key performance",
        "activity measure",
        "methodology",
        "approach",
        "scope of work",
        "monitoring",
        "evaluation",
        "measurement framework",
    )
    seen: set[str] = set()
    result: list[ProposalSection] = []
    for section in draft.sections:
        title_cf = (section.title or "").casefold()
        blob = _section_blob(section).casefold()
        if any(n in title_cf or n in blob for n in needles):
            if section.id not in seen and (section.content or "").strip():
                seen.add(section.id)
                result.append(section)
    return result


def _budget_related_sections(draft: ProposalDraft) -> list[ProposalSection]:
    idx = find_budget_section_index(draft.sections)
    sections: list[ProposalSection] = []
    if idx is not None:
        sections.append(draft.sections[idx])
    for section in draft.sections:
        title_cf = (section.title or "").casefold()
        if any(
            k in title_cf
            for k in (
                "budget",
                "pricing",
                "cost factor",
                "fee",
                "price reasonableness",
                "compensation",
            )
        ):
            if section.id not in {s.id for s in sections} and (section.content or "").strip():
                sections.append(section)
    return sections


def parse_scoring_facts_from_rfp(rfp_text: str) -> RfpScoringFacts:
    text = rfp_text or ""
    facts = RfpScoringFacts()
    facts.cost_scoring_inverse = bool(_INVERSE_COST_SCORING_RE.search(text))

    m_contractor = re.search(
        r"(\d+|three|two|four)\s*\(?\d*\)?\s*key performance indicators?\s*that\s*the\s*contractor",
        text,
        re.I,
    )
    if m_contractor:
        token = m_contractor.group(1).casefold()
        mapping = {"three": 3, "two": 2, "four": 4}
        if token in mapping:
            facts.contractor_kpi_count = mapping[token]
        elif token.isdigit():
            facts.contractor_kpi_count = int(token)

    m_c4 = re.search(r"criteria\s*#?\s*4[^.\n]{0,120}?(\d+)\s*points?", text, re.I)
    m_c5 = re.search(r"criteria\s*#?\s*5[^.\n]{0,120}?(\d+)\s*points?", text, re.I)
    if m_c4:
        facts.cost_points_criteria_4 = int(m_c4.group(1))
    if m_c5:
        facts.cost_points_criteria_5 = int(m_c5.group(1))
    if facts.cost_points_criteria_4 is not None and facts.cost_points_criteria_5 is not None:
        facts.cost_points_combined = facts.cost_points_criteria_4 + facts.cost_points_criteria_5

    if _EXCEL_ATTACHMENT_RE.search(text):
        facts.budget_submission_format = (
            "Separate budget attachment (often Excel worksheet / Attachment 01) — "
            "not a long narrative cost table inside the main proposal PDF."
        )

    return facts


async def extract_rfp_scoring_facts_llm(rfp_excerpt: str) -> RfpScoringFacts:
    base = parse_scoring_facts_from_rfp(rfp_excerpt)
    if not llm.is_configured() or len(rfp_excerpt.strip()) < 200:
        return base

    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract scoring facts from THIS RFP only. Distinguish:\n"
                        "- KPIs the CONTRACTOR must hit\n"
                        "- KPIs that belong to the AGENCY/buyer (not contract scorecard)\n"
                        "- Cost scoring: inverse (lowest price = max points) vs other\n"
                        "- Criteria point weights for cost/price\n"
                        "- Required budget FILE format (Attachment Excel, etc.)\n"
                        "Return JSON:\n"
                        "{\n"
                        '  "contractorKpiCount": number|null,\n'
                        '  "contractorKpiSummary": "string",\n'
                        '  "agencyKpiNote": "string",\n'
                        '  "costScoringInverse": boolean,\n'
                        '  "costScoringSummary": "string",\n'
                        '  "costPointsCriteria4": number|null,\n'
                        '  "costPointsCriteria5": number|null,\n'
                        '  "budgetSubmissionFormat": "string"\n'
                        "}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"RFP excerpt:\n{rfp_excerpt[:38000]}",
                },
            ],
            max_tokens=2048,
            temperature=0.1,
        )
        data = raw or {}
        count = data.get("contractorKpiCount")
        c4 = data.get("costPointsCriteria4")
        c5 = data.get("costPointsCriteria5")
        combined = None
        if isinstance(c4, (int, float)) and isinstance(c5, (int, float)):
            combined = int(c4) + int(c5)
        return RfpScoringFacts(
            contractor_kpi_count=(
                int(count) if isinstance(count, (int, float)) else base.contractor_kpi_count
            ),
            contractor_kpi_summary=str(data.get("contractorKpiSummary") or "")[:2000],
            agency_kpi_note=str(data.get("agencyKpiNote") or "")[:1500],
            cost_scoring_inverse=bool(data.get("costScoringInverse", base.cost_scoring_inverse)),
            cost_scoring_summary=str(data.get("costScoringSummary") or "")[:1500],
            cost_points_criteria_4=(
                int(c4) if isinstance(c4, (int, float)) else base.cost_points_criteria_4
            ),
            cost_points_criteria_5=(
                int(c5) if isinstance(c5, (int, float)) else base.cost_points_criteria_5
            ),
            cost_points_combined=combined or base.cost_points_combined,
            budget_submission_format=str(
                data.get("budgetSubmissionFormat") or base.budget_submission_format
            )[:1500],
            raw=data if isinstance(data, dict) else {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM scoring fact extract failed: %s", exc)
        return base


def scan_draft_accuracy_findings(
    draft: ProposalDraft,
    facts: RfpScoringFacts,
    rfp_text: str,
) -> list[AccuracyFinding]:
    findings: list[AccuracyFinding] = []
    manuscript = "\n\n".join(
        _section_blob(s) for s in draft.sections if (s.content or "").strip()
    )
    manuscript_cf = manuscript.casefold()

    kpi_sections = _kpi_related_sections(draft)
    budget_sections = _budget_related_sections(draft)

    from app.services.proposal_fulfill_rfp_budget_kpi import rfp_requires_contractor_kpi_alignment

    rfp_contractor_kpis = (
        facts.contractor_kpi_count == 3
        or _CONTRACTOR_KPI_CUE_RE.search(rfp_text or "")
        or rfp_requires_contractor_kpi_alignment(rfp_text or "")
    )
    if rfp_contractor_kpis:
        from app.services.proposal_fulfill_kpi_detail import content_has_kpi_detail_artifacts
        from app.services.proposal_fulfill_rfp_repairs import sections_with_wrong_kpi_framework

        wrong_ids = sections_with_wrong_kpi_framework(draft)
        detail_artifact_ids = [
            s.id
            for s in draft.sections
            if (s.content or "").strip() and content_has_kpi_detail_artifacts(s.content or "")
        ]
        agency_hits = [cue for cue in _AGENCY_KPI_CUES if cue in manuscript_cf]
        wrong_four = bool(
            re.search(r"\bfour\b.{0,56}\bkpi", manuscript_cf)
            or "four headline kpi" in manuscript_cf
        )
        if wrong_ids or agency_hits or wrong_four or detail_artifact_ids:
            ids = list(dict.fromkeys([*wrong_ids, *detail_artifact_ids])) or [s.id for s in kpi_sections]
            if not ids:
                from app.services.proposal_fulfill_kpi_fix import content_uses_agency_kpi_framework

                ids = [
                    s.id
                    for s in draft.sections
                    if (s.content or "").strip()
                    and content_uses_agency_kpi_framework(s.content or "")
                ]
            if not ids and (agency_hits or wrong_four):
                ids = [
                    s.id
                    for s in draft.sections
                    if (s.content or "").strip()
                    and (
                        "kpi" in _section_blob(s).casefold()
                        or "activity measure" in _section_blob(s).casefold()
                        or "methodology" in _section_blob(s).casefold()
                        or "market knowledge" in _section_blob(s).casefold()
                    )
                ]
            findings.append(
                AccuracyFinding(
                    kind="kpi_scope",
                    message=(
                        "Draft uses HTA/agency four-KPI language and/or fabricated KPI measurement "
                        "(e.g. 'Total Visitor Arrivals Survey' with resident sentiment text). "
                        "RFP Section 2.3 contractor-scored KPIs are ONLY Total Visitor Arrivals "
                        "(+3.0%/yr), Total Visitor Expenditures (+4.6%/yr), and Average Islands "
                        "Visited Per Person (+0.8%/yr). Rewrite Activity Measure rows and BMP "
                        "KPI linkages with correct data sources — not label-swapped surveys."
                    ),
                    section_ids=ids,
                )
            )

    if facts.cost_scoring_inverse or _INVERSE_COST_SCORING_RE.search(rfp_text or ""):
        if _COST_CEILING_AS_WIN_RE.search(manuscript):
            findings.append(
                AccuracyFinding(
                    kind="cost_scoring",
                    message=(
                        "Draft treats ceiling/max price as maximizing cost score. "
                        "RFP uses inverse scoring (lowest responsive price gets most points)."
                    ),
                    section_ids=[s.id for s in budget_sections],
                )
            )

    if facts.cost_points_combined and facts.cost_points_combined >= 12:
        if re.search(r"\b10\s*%\s*(?:weight|of\s+(?:total|available)\s+points)", manuscript, re.I):
            findings.append(
                AccuracyFinding(
                    kind="cost_weight",
                    message=(
                        f"Draft may understate cost weight — RFP suggests "
                        f"{facts.cost_points_combined} combined cost/price points."
                    ),
                    section_ids=[s.id for s in budget_sections],
                )
            )

    if facts.budget_submission_format or _EXCEL_ATTACHMENT_RE.search(rfp_text or ""):
        budget_blob = "\n".join(_section_blob(s) for s in budget_sections)
        if len(budget_blob) > 800:
            bl = budget_blob.casefold()
            if "attachment 01" not in bl and "excel" not in bl and "worksheet" not in bl:
                findings.append(
                    AccuracyFinding(
                        kind="budget_container",
                        message=(
                            "Budget is narrative-only. RFP requires separate budget attachment "
                            "(often Attachment 01 Excel) — narrative should not replace the worksheet."
                        ),
                        section_ids=[s.id for s in budget_sections],
                    )
                )

    return findings


async def _redraft_section_for_accuracy(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_excerpt: str,
    facts: RfpScoringFacts,
    findings: list[AccuracyFinding],
    budget_notes: str,
) -> str:
    stub = section.content or ""
    finding_text = "\n".join(f"- [{f.kind}] {f.message}" for f in findings)
    facts_json = json.dumps(
        {
            "contractorKpiCount": facts.contractor_kpi_count,
            "contractorKpiSummary": facts.contractor_kpi_summary,
            "agencyKpiNote": facts.agency_kpi_note,
            "costScoringInverse": facts.cost_scoring_inverse,
            "costScoringSummary": facts.cost_scoring_summary,
            "costPointsCombined": facts.cost_points_combined,
            "budgetSubmissionFormat": facts.budget_submission_format,
        },
        indent=2,
    )

    if not llm.is_configured():
        return stub

    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Rewrite ONE proposal section to match THIS RFP scoring structure.\n"
                        "- CONTRACTOR KPIs only — not buyer agency strategic-plan KPIs.\n"
                        "- If draft says four HTA/agency KPIs (Resident Sentiment, Visitor "
                        "Satisfaction, Average Daily Visitor Spending, Total Visitor Expenditures "
                        "as agency scorecard), replace with RFP Section 2.3 contractor KPIs only: "
                        "Total Visitor Arrivals (+3.0%/yr), Total Visitor Expenditures (+4.6%/yr), "
                        "Average Islands Visited Per Person (+0.8%/yr).\n"
                        "- Do NOT rewrite team bios or unrelated company facts.\n"
                        "- Inverse cost scoring: never claim ceiling price earns max cost points.\n"
                        "- Use correct cost/price point weights from RFP.\n"
                        "- Budget: reference Attachment/Excel; narrative is cover only.\n"
                        "Return JSON: {\"content\": \"markdown\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
                        f"Section: {section.title} ({section.id})\n\n"
                        f"Fix:\n{finding_text}\n\n"
                        f"Facts:\n{facts_json}\n\n"
                        f"Budget notes:\n{budget_notes[:2500]}\n\n"
                        f"RFP excerpt:\n{rfp_excerpt[:30000]}\n\n"
                        f"Current:\n{(section.content or '')[:12000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.2,
        )
        content = str((raw or {}).get("content") or "").strip()
        return content or stub
    except Exception as exc:  # noqa: BLE001
        logger.warning("Accuracy redraft failed for %s: %s", section.id, exc)
        return stub


async def run_rfp_accuracy_fulfill_pass(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
    skip_section_ids: set[str] | None = None,
) -> tuple[ProposalDraft, list[str], list[str]]:
    excerpt = evaluation_and_kpi_excerpt(rfp_text)
    facts = await extract_rfp_scoring_facts_llm(excerpt or rfp_text[:50000])
    findings = scan_draft_accuracy_findings(draft, facts, rfp_text)

    logs: list[str] = []
    human_gaps: list[str] = []
    if not findings:
        logs.append("RFP accuracy scan: no KPI/cost/budget-container mismatches detected.")
        return draft, logs, human_gaps

    logs.append(f"RFP accuracy scan: {len(findings)} issue group(s) — repairing sections.")

    budget_notes = ""
    if research and research.budget:
        b = research.budget
        budget_notes = (
            f"Tier: {b.pricing_tier}; cap: {b.rfp_budget_cap}; "
            f"agency revenue: {b.agency_revenue_estimate}; "
            f"flags: {'; '.join((b.pricing_flags or [])[:6])}"
        )

    target_ids: set[str] = set()
    for finding in findings:
        target_ids.update(finding.section_ids)

    # KPI mismatch: repair every section that still uses agency/four-KPI language.
    if any(f.kind == "kpi_scope" for f in findings):
        from app.services.proposal_fulfill_rfp_repairs import sections_with_wrong_kpi_framework

        target_ids.update(sections_with_wrong_kpi_framework(draft))

    skip = skip_section_ids or set()
    sections = list(draft.sections)
    changed = False

    # Deterministic KPI spine first — LLM section rewrites often miss stray "four KPI" lines.
    if any(f.kind == "kpi_scope" for f in findings):
        from app.services.proposal_fulfill_kpi_fix import apply_contractor_kpi_text_fixes
        from app.services.proposal_fulfill_rfp_repairs import sections_with_wrong_kpi_framework

        for idx, section in enumerate(sections):
            if section.id in skip:
                continue
            fixed, fix_logs = apply_contractor_kpi_text_fixes(section.content or "")
            if fixed != (section.content or ""):
                sections[idx] = section.model_copy(
                    update={"content": fixed, "status": "generated"}
                )
                changed = True
                logs.extend(f"KPI deterministic: {section.title}" for _ in fix_logs[:1])
        target_ids.update(sections_with_wrong_kpi_framework(
            draft.model_copy(update={"sections": sections})
        ))

    for idx, section in enumerate(sections):
        if section.id not in target_ids:
            continue
        if section.id in skip:
            logs.append(f"Accuracy repair skipped (preserved): {section.title}")
            continue
        relevant = [f for f in findings if section.id in f.section_ids]
        kpi_wrong = section.id in sections_with_wrong_kpi_framework(
            draft.model_copy(update={"sections": sections})
        ) if any(f.kind == "kpi_scope" for f in findings) else False
        if not relevant and not kpi_wrong:
            continue
        if not relevant and kpi_wrong:
            relevant = [f for f in findings if f.kind == "kpi_scope"]
        # KPI fixes are deterministic only — LLM section rewrites re-introduced agency four-KPI language.
        if relevant and all(f.kind == "kpi_scope" for f in relevant):
            continue
        new_content = await _redraft_section_for_accuracy(
            section=section,
            rfp=rfp,
            rfp_excerpt=excerpt or rfp_text,
            facts=facts,
            findings=relevant,
            budget_notes=budget_notes,
        )
        if new_content.strip() and new_content.strip() != (section.content or "").strip():
            sections[idx] = section.model_copy(
                update={"content": new_content, "status": "generated"}
            )
            changed = True
            logs.append(
                f"Accuracy repair: {section.title} — {', '.join(f.kind for f in relevant)}"
            )

    if facts.cost_scoring_inverse and research and research.budget:
        cap = research.budget.rfp_budget_cap
        rev = research.budget.agency_revenue_estimate or research.budget.lump_sum_total
        if cap and rev and float(rev) >= float(cap) * 0.98:
            human_gaps.append(
                "Price is at/near the RFP ceiling under inverse cost scoring — expect lower "
                "cost points unless leadership chooses a more competitive bid."
            )

    if not changed:
        logs.append("RFP accuracy: findings logged but section text unchanged.")
        return draft, logs, human_gaps

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs, human_gaps
