"""Complete & Clean — Senior Editor as RFP proposal reviewer.

The manuscript director reads THIS RFP + full draft digest, emits tickets
(dedupe, coverage gaps, compliance), and applies dedupe/delete only.
Coverage/compliance gaps are reported — not silently invented.
"""

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
    """One Senior Editor read of the full manuscript — reviewer, not regen-from-scratch."""
    from app.services.proposal_langchain_agents import senior_editor_emit_tickets
    from app.services.proposal_self_edit_loop import (
        SelfEditReport,
        _apply_senior_editor_tickets,
        _manuscript_digest_for_senior_editor,
    )

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
        )
    )
    if coverage_audit_logs:
        for line in coverage_audit_logs[:16]:
            report.logs.append(f"coverage-audit: {line}")

    digest = _manuscript_digest_for_senior_editor(draft)
    req_map = _requirements_by_section(draft, research)

    tickets = await senior_editor_emit_tickets(
        rfp_client=rfp.client or "",
        rfp_title=rfp.title or "",
        manuscript_digest=digest,
        requirements_by_section=req_map,
    )

    report.delete_tickets = len(tickets.get("deleteSectionTickets") or [])
    report.dedupe_tickets = len(tickets.get("dedupeTickets") or [])
    report.coverage_gaps = _gaps_from_tickets(
        list(tickets.get("coverageTickets") or []), kind="coverage"
    )
    if mechanical_coverage:
        mech_gaps = _gaps_from_tickets(mechanical_coverage, kind="coverage")
        seen = set(report.coverage_gaps)
        for gap in mech_gaps:
            if gap not in seen:
                report.coverage_gaps.append(gap)
                seen.add(gap)
    report.compliance_gaps = _gaps_from_tickets(
        list(tickets.get("complianceTickets") or []), kind="compliance"
    )
    report.notes = [str(n) for n in (tickets.get("notes") or []) if str(n).strip()]

    # Reviewer applies structural fixes only — never coverage redrafts that invent.
    apply_payload = {
        "deleteSectionTickets": tickets.get("deleteSectionTickets") or [],
        "dedupeTickets": tickets.get("dedupeTickets") or [],
        "coverageTickets": [],
        "complianceTickets": [],
        "compactFormatTickets": [],
    }

    edit_report = SelfEditReport(iterations_run=0)
    draft, research = await _apply_senior_editor_tickets(
        tickets=apply_payload,
        rfp_id=rfp_id,
        rfp=rfp,
        draft=draft,
        research=research,
        report=edit_report,
        max_tickets=max_dedupe_tickets,
    )
    report.sections_improved = edit_report.sections_improved

    for entry in edit_report.section_logs:
        detail = entry.get("detail") if isinstance(entry, dict) else str(entry)
        if detail:
            report.logs.append(f"reviewer: {detail}")

    if report.dedupe_tickets or report.delete_tickets:
        report.logs.insert(
            0,
            f"Senior reviewer: {report.delete_tickets} delete / "
            f"{report.dedupe_tickets} dedupe ticket(s); "
            f"applied {edit_report.sections_improved} trim(s).",
        )
    if report.coverage_gaps:
        report.logs.append(
            f"Senior reviewer: {len(report.coverage_gaps)} coverage gap(s) "
            "flagged (not auto-drafted — needs KB or human)."
        )
    if report.compliance_gaps:
        report.logs.append(
            f"Senior reviewer: {len(report.compliance_gaps)} compliance gap(s) flagged."
        )

    del rfp_text  # used for mechanical coverage audit + future digest excerpt
    return draft, research, report
