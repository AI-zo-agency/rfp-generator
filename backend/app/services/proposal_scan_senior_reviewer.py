"""Complete Scan senior-editor step — coverage audit, not a second rewrite pass."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord

logger = logging.getLogger(__name__)


@dataclass
class ScanReviewerReport:
    delete_tickets: int = 0
    dedupe_tickets: int = 0
    coverage_gaps: list[str] = field(default_factory=list)
    compliance_gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sections_improved: int = 0
    logs: list[str] = field(default_factory=list)


def _requirements_by_section(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    # Guard `research` before touching .rfp_sections — the comprehension's `if`
    # filters m, so it does not protect this attribute access.
    mapped = {m.id: m for m in ((research.rfp_sections or []) if research else [])}
    for section in draft.sections:
        m = mapped.get(section.id)
        # Requirements live only on RfpSectionMap. ProposalSection has no
        # `requirements` field, so there is no per-section fallback to read:
        # sections Scan adds (closing tabs, submission forms) are simply absent
        # from research.rfp_sections and carry no requirements of their own.
        if m and m.requirements:
            out[section.id] = list(m.requirements)
    return out


def _gaps_from_tickets(tickets: list[dict[str, Any]], *, kind: str) -> list[str]:
    gaps: list[str] = []
    for t in tickets:
        if not isinstance(t, dict):
            continue
        sid = str(t.get("sectionId") or "").strip()
        title_hint = sid
        brief = str(
            t.get("rewriteBrief")
            or t.get("policyOrGuideline")
            or t.get("reason")
            or ""
        ).strip()
        unmet = t.get("unmetRequirements")
        if isinstance(unmet, list) and unmet:
            gaps.append(
                f"{kind}:{title_hint} — "
                + "; ".join(str(u) for u in unmet[:4])[:280]
            )
        elif brief:
            gaps.append(f"{kind}:{title_hint} — {brief[:240]}")
    return gaps


async def run_complete_scan_senior_reviewer(
    *,
    rfp_id: str,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_text: str = "",
    max_dedupe_tickets: int = 8,
) -> tuple[ProposalDraft, ProposalResearchCache | None, ScanReviewerReport]:
    """Coverage audit only — Generate + Scan step 5 already trimmed overlap.

    Does not emit senior-editor LLM tickets or rewrite tabs. Quality-gate /
    fact-check / line-grounding still own factual accuracy later in Scan.
    """
    del rfp_id, max_dedupe_tickets

    report = ScanReviewerReport()
    from app.services.proposal_senior_editor_coverage import (
        apply_senior_editor_section_coverage_audit,
    )

    draft, coverage_audit_logs, mechanical_coverage = (
        await apply_senior_editor_section_coverage_audit(
            draft,
            research=research,
            rfp_text=rfp_text or "",
            rfp_title=rfp.title or "",
            use_llm_toc=not bool(research and research.rfp_sections),
        )
    )
    if coverage_audit_logs:
        for line in coverage_audit_logs[:16]:
            report.logs.append(f"coverage-audit: {line}")

    report.coverage_gaps = _gaps_from_tickets(mechanical_coverage, kind="coverage")
    report.notes = [
        "scan: skipped senior-editor LLM emit — duplicates Generate polish + "
        "mechanical dedupe; coverage gaps flagged from the outline audit"
    ]
    report.logs.append(report.notes[0])
    return draft, research, report
