"""Pipeline completion checks — all phases done, no VERIFY stubs in manuscript."""

from __future__ import annotations

import logging
import re

from app.core.config import settings
from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

_VERIFY_RE = re.compile(r"\[VERIFY:", re.I)
_DRAFT_ERROR_RE = re.compile(
    r"section drafting failed|needs manual regeneration|invalid json|llm returned",
    re.I,
)


def count_verify_tags(draft: ProposalDraft) -> int:
    return sum(len(_VERIFY_RE.findall(s.content or "")) for s in draft.sections)


def _consistency_critical_blockers(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[str]:
    """Re-scan manuscript consistency and promote critical issues to blockers.

    Readiness does not consume persisted presubmit_review issue content — only
    attachment presence — so blocking requires a fresh scan (OQ-4).
    """
    from app.services.proposal_consistency import scan_manuscript_consistency

    issues = scan_manuscript_consistency(draft=draft, research=research, rfp=rfp)
    blockers: list[str] = []
    for issue in issues:
        if issue.severity != "critical":
            continue
        loc = issue.section_title or issue.section_id or "manuscript"
        blockers.append(
            f"Consistency critical ({issue.category}) in {loc}: {issue.message}"
        )
    if blockers:
        logger.info(
            "consistency_critical_blockers count=%s rfp_id=%s",
            len(blockers),
            rfp.id,
        )
    return blockers


def _t1_gate_blockers(draft: ProposalDraft) -> list[str]:
    from app.services.proposal_t1_validators import (
        scan_all_t1,
        t1_findings_as_blocker_messages,
    )

    findings = scan_all_t1(draft)
    return t1_findings_as_blocker_messages(findings)


def collect_manuscript_blockers(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord | None = None,
    require_budget: bool = True,
) -> list[str]:
    blockers: list[str] = []

    if not research:
        blockers.append("Phase 2 incomplete — no research cache.")
    else:
        plan = research.proposal_execution_plan
        plan_ready = False
        if plan is not None and hasattr(plan, "validation"):
            plan_ready = plan.validation.readiness_status == "ready"
        elif isinstance(plan, dict):
            plan_ready = (plan.get("validation") or {}).get("readinessStatus") == "ready"
        if not plan_ready and not research.evidence_corpus:
            blockers.append(
                "Phase 2 incomplete — Proposal Execution Plan not ready."
            )

    mapped = research.rfp_sections if research else []
    mapped_ids = {s.id for s in mapped}
    for section in draft.sections:
        if section.id not in mapped_ids:
            continue
        if not section.content.strip():
            blockers.append(f"Section blank: {section.title}")
            continue
        if _DRAFT_ERROR_RE.search(section.content):
            blockers.append(f"Section has system error text: {section.title}")
        verify_n = len(_VERIFY_RE.findall(section.content))
        if verify_n > 0:
            blockers.append(
                f"Section has {verify_n} unresolved [VERIFY] tag(s): {section.title}"
            )

    if require_budget:
        if not research or not research.budget:
            blockers.append("Phase 3.5 incomplete — no budget generated.")
        elif research.budget.agency_revenue_estimate is None:
            blockers.append("Budget missing agency revenue estimate.")

    if research and not research.presubmit_review:
        blockers.append("Phase 4 review not attached — run pre-submit review.")

    if research and mapped and not research.proof_points:
        blockers.append("Phase 2: no proof points matched to RFP requirements.")

    # Optional flagged gates — never weaken the existing blocker list above.
    if settings.t1_gates_block:
        blockers.extend(_t1_gate_blockers(draft))

    if settings.consistency_criticals_block and rfp is not None:
        blockers.extend(
            _consistency_critical_blockers(draft=draft, research=research, rfp=rfp)
        )

    return blockers


def assert_manuscript_ready(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord | None = None,
    require_budget: bool = True,
) -> None:
    blockers = collect_manuscript_blockers(
        draft=draft,
        research=research,
        rfp=rfp,
        require_budget=require_budget,
    )
    if blockers:
        summary = "; ".join(blockers[:6])
        if len(blockers) > 6:
            summary += f"; +{len(blockers) - 6} more"
        raise ProposalError(
            f"Proposal pipeline incomplete: {summary}",
            status_code=422,
        )
