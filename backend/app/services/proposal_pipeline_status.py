"""Pipeline completion checks — all phases done, no VERIFY stubs in manuscript."""

from __future__ import annotations

import re

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError

_VERIFY_RE = re.compile(r"\[VERIFY:", re.I)
_DRAFT_ERROR_RE = re.compile(
    r"section drafting failed|needs manual regeneration|invalid json|llm returned",
    re.I,
)


def count_verify_tags(draft: ProposalDraft) -> int:
    return sum(len(_VERIFY_RE.findall(s.content or "")) for s in draft.sections)


def collect_manuscript_blockers(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord | None = None,
    require_budget: bool = True,
) -> list[str]:
    blockers: list[str] = []

    if not research or not research.evidence_corpus:
        blockers.append("Phase 2 incomplete — no evidence corpus (run retrieval).")

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
