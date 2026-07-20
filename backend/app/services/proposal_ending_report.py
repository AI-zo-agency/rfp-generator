"""Final RFP ending report — manuscript close-out after budget + review."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import (
    requirement_likely_covered,
    scan_rfp_compliance_gaps,
)
from app.services.proposal_section_quality import word_count

logger = logging.getLogger(__name__)


class EndingRequirementStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    section_title: str = Field(alias="sectionTitle")
    requirement: str
    covered: bool
    evaluation_weight: int | None = Field(default=None, alias="evaluationWeight")


class ProposalEndingReport(BaseModel):
    """What the proposal ends with after Budget — not just 'done', a close-out brief."""

    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(alias="rfpId")
    rfp_title: str = Field(alias="rfpTitle")
    rfp_client: str = Field(alias="rfpClient")
    generated_at: str = Field(alias="generatedAt")

    pipeline_order: list[str] = Field(
        default_factory=list,
        alias="pipelineOrder",
        description="Human-readable phase order through proposal close",
    )
    ends_with: str = Field(
        default="",
        alias="endsWith",
        description="What comes after Budget in the submission package",
    )

    static_sections_count: int = Field(default=0, alias="staticSectionsCount")
    rfp_mapped_sections_count: int = Field(default=0, alias="rfpMappedSectionsCount")
    drafted_sections_count: int = Field(default=0, alias="draftedSectionsCount")
    total_words: int = Field(default=0, alias="totalWords")

    has_budget: bool = Field(default=False, alias="hasBudget")
    budget_tier: str | None = Field(default=None, alias="budgetTier")
    agency_revenue: float | None = Field(default=None, alias="agencyRevenue")

    requirements_total: int = Field(default=0, alias="requirementsTotal")
    requirements_covered: int = Field(default=0, alias="requirementsCovered")
    requirements_uncovered: int = Field(default=0, alias="requirementsUncovered")
    requirement_statuses: list[EndingRequirementStatus] = Field(
        default_factory=list, alias="requirementStatuses"
    )

    compliance_gaps: int = Field(default=0, alias="complianceGaps")
    presubmit_issues: int = Field(default=0, alias="presubmitIssues")
    ready_to_submit: bool = Field(default=False, alias="readyToSubmit")

    summary_markdown: str = Field(default="", alias="summaryMarkdown")
    next_actions: list[str] = Field(default_factory=list, alias="nextActions")


PIPELINE_CLOSE_ORDER = [
    "1. Sections 1–3 (zö static: company / team / experience)",
    "2. Phase 2 — Research RFP (map every RFP-demanded tab + retrieve KB)",
    "3. Phase 3 — Draft RFP tabs (methodology, timeline, etc. ONLY if RFP asks)",
    "4. Senior editor polish",
    "5. Budget",
    "6. Pre-submit review + Ending report (EXPORT / handoff)",
]


def build_proposal_ending_report(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> ProposalEndingReport:
    """Build the close-out report that sits after Budget in the pipeline."""
    now = datetime.now(timezone.utc).isoformat()
    sections = draft.sections if draft else []
    drafted = [s for s in sections if (s.content or "").strip()]
    total_words = sum(word_count(s.content or "") for s in drafted)

    static_ids = {
        "section-1-who-we-are",
        "section-1-org-structure",
        "section-1-business-info",
        "section-1-certifications",
        "section-1-insurance",
        "section-1-company-overview",
        "section-2-team-overview",
        "section-3-our-work",
    }
    static_count = sum(1 for s in drafted if s.id in static_ids or s.id.startswith("section-1-") or s.id.startswith("section-2-") or s.id.startswith("section-3-"))

    rfp_mapped = research.rfp_sections if research else []
    manuscript = "\n\n".join(
        f"## {s.title}\n{s.content}" for s in drafted
    )

    statuses: list[EndingRequirementStatus] = []
    covered = 0
    total_reqs = 0
    for mapped in rfp_mapped:
        for req in mapped.requirements or []:
            text = str(req).strip()
            if not text:
                continue
            total_reqs += 1
            is_covered = requirement_likely_covered(text, manuscript)
            if is_covered:
                covered += 1
            statuses.append(
                EndingRequirementStatus(
                    sectionId=mapped.id,
                    sectionTitle=mapped.title,
                    requirement=text,
                    covered=is_covered,
                    evaluationWeight=mapped.evaluation_weight,
                )
            )

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp) if research else []
    review = research.presubmit_review if research else None
    budget = research.budget if research else None

    next_actions: list[str] = []
    if not budget:
        next_actions.append("Build Budget (Phase 3.5) before export.")
    if review and not review.ready_to_submit:
        next_actions.append(
            f"Resolve {len(review.issues)} pre-submit issue(s) before export."
        )
    uncovered_n = total_reqs - covered
    if uncovered_n > 0:
        next_actions.append(
            f"Address {uncovered_n} uncovered RFP requirement(s) — see requirementStatuses."
        )
    if gaps:
        next_actions.append(f"Close {len(gaps)} compliance gap(s) flagged from the RFP map.")
    if not next_actions:
        next_actions.append("Ready for EXPORT — package manuscript + budget for submission.")

    ends_with = (
        "After Budget → Pre-submit REVIEW → Ending report → EXPORT. "
        "There is no automatic cover letter unless the RFP mapped one in Phase 2."
    )

    summary = _format_summary_markdown(
        client=rfp.client,
        title=rfp.title,
        drafted_n=len(drafted),
        mapped_n=len(rfp_mapped),
        total_words=total_words,
        covered=covered,
        total_reqs=total_reqs,
        has_budget=budget is not None,
        tier=budget.pricing_tier if budget else None,
        ready=bool(review and review.ready_to_submit),
        next_actions=next_actions,
    )

    report = ProposalEndingReport(
        rfpId=rfp.id,
        rfpTitle=rfp.title,
        rfpClient=rfp.client,
        generatedAt=now,
        pipelineOrder=list(PIPELINE_CLOSE_ORDER),
        endsWith=ends_with,
        staticSectionsCount=static_count,
        rfpMappedSectionsCount=len(rfp_mapped),
        draftedSectionsCount=len(drafted),
        totalWords=total_words,
        hasBudget=budget is not None,
        budgetTier=budget.pricing_tier if budget else None,
        agencyRevenue=budget.agency_revenue_estimate if budget else None,
        requirementsTotal=total_reqs,
        requirementsCovered=covered,
        requirementsUncovered=uncovered_n,
        requirementStatuses=statuses,
        complianceGaps=len(gaps),
        presubmitIssues=len(review.issues) if review else 0,
        readyToSubmit=bool(review and review.ready_to_submit),
        summaryMarkdown=summary,
        nextActions=next_actions,
    )
    logger.info(
        "Ending report for %s: %d/%d reqs covered, budget=%s, ready=%s",
        rfp.id,
        covered,
        total_reqs,
        bool(budget),
        report.ready_to_submit,
    )
    return report


def _format_summary_markdown(
    *,
    client: str,
    title: str,
    drafted_n: int,
    mapped_n: int,
    total_words: int,
    covered: int,
    total_reqs: int,
    has_budget: bool,
    tier: str | None,
    ready: bool,
    next_actions: list[str],
) -> str:
    lines = [
        f"# Proposal Ending Report — {client}",
        "",
        f"**RFP:** {title}",
        "",
        "## How this proposal ends",
        "1. Static zö Sections 1–3 (company / team / experience)",
        "2. RFP-mapped tabs from Phase 2 research (only what THIS RFP demands)",
        "3. Senior editor polish",
        "4. **Budget**",
        "5. Pre-submit REVIEW + this ending report",
        "6. **EXPORT** for submission",
        "",
        "## Package status",
        f"- Drafted sections: **{drafted_n}** (RFP-mapped tabs: {mapped_n})",
        f"- Manuscript words: **{total_words:,}**",
        f"- RFP requirements covered: **{covered}/{total_reqs}**",
        f"- Budget: **{'yes — ' + (tier or 'built') if has_budget else 'missing'}**",
        f"- Ready to submit: **{'yes' if ready else 'not yet'}**",
        "",
        "## Next actions",
    ]
    for action in next_actions:
        lines.append(f"- {action}")
    return "\n".join(lines)


def ending_report_as_dict(report: ProposalEndingReport) -> dict[str, Any]:
    return report.model_dump(by_alias=True)
