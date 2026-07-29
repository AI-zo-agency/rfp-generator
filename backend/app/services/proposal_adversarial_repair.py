"""Bounded validate -> repair -> re-validate loop for manuscript audit findings."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone

from app.core.config import settings
from app.models.proposal import (
    AdversarialAuditFinding,
    AdversarialRepairAttempt,
    AdversarialRepairReport,
    PreSubmitIssue,
    ProposalAdversarialAudit,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.evidence_trust.legal_attestation_gate import (
    gate_section_legal_attestations,
)
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_manual_flags import (
    VERIFY_TAG_RE,
    _owner_for_field,
    extract_manual_fill_tags,
    manual_fill_tags_preserved,
)
from app.services.proposal_manuscript_auditor import (
    persist_manuscript_audit,
    run_manuscript_auditor,
)

logger = logging.getLogger(__name__)

_SWORN_OR_LEGAL_RE = re.compile(
    r"e-?verify|conflict of interest|penalty of perjury|sworn|affidavit|attestation",
    re.I,
)
_VERIFY_TEXT_RE = re.compile(r"\[VERIFY:[^\]]+\]", re.I)
_OPEN_PRICING_HANDOFFS_ID = "adversarial-open-pricing-handoffs"

_NON_CONVERGENCE_REASONS = frozenset(
    {
        "no_progress",
        "time_budget_exceeded",
        "max_rounds",
        "attempts_exhausted",
        "manual_fill_required",
    }
)


def _finding_key(audit: object) -> str:
    code = str(getattr(audit, "code", "") or "")
    section_id = str(getattr(audit, "section_id", "") or "")
    message = str(getattr(audit, "message", "") or "")
    return f"{code}::{section_id}::{message}"


def _finding_family(finding: AdversarialAuditFinding) -> str:
    """Collapse duplicate audit+integrity codes for the same underlying defect."""
    code = (finding.code or "").casefold()
    message = (finding.message or "").casefold()
    blob = f"{code} {message}"
    if "staffing_hours" in blob or "staffing hours" in blob:
        return "staffing_hours"
    if "percent_time" in blob or "percent time" in blob:
        return "percent_time"
    if "orphan" in blob and "commission" in blob:
        return "orphan_commission"
    if "free_currency" in blob:
        return "free_currency"
    if "note_leak" in blob:
        return "note_leak"
    if "truncat" in blob:
        return "truncation"
    return f"{code}::{message[:96]}"


def _dedupe_actionable_findings(
    findings: list[AdversarialAuditFinding],
) -> list[AdversarialAuditFinding]:
    seen: set[tuple[str, str]] = set()
    deduped: list[AdversarialAuditFinding] = []
    for finding in findings:
        key = (finding.section_id or "", _finding_family(finding))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _section_map(draft: ProposalDraft) -> dict[str, ProposalSection]:
    return {section.id: section for section in draft.sections}


def _protected_finding(section: ProposalSection | None, message: str) -> bool:
    if _SWORN_OR_LEGAL_RE.search(message):
        return True
    if section is None:
        return False
    content = section.content or ""
    if extract_manual_fill_tags(content):
        return True
    return bool(_VERIFY_TEXT_RE.search(content))


def _deterministic_repair(section: ProposalSection) -> ProposalSection:
    repaired, _report = gate_section_legal_attestations(section, force=True)
    return repaired


def _deterministic_fixable_findings(
    draft: ProposalDraft,
) -> list[AdversarialAuditFinding]:
    findings: list[AdversarialAuditFinding] = []
    for section in draft.sections:
        _patched, report = gate_section_legal_attestations(section, force=True)
        if report.hours_flags:
            findings.append(
                AdversarialAuditFinding(
                    severity="critical",
                    category="consistency",
                    code="deterministic.integrity.staffing_hours",
                    message="Unverified staffing hours must be converted to [VERIFY: staffing hours].",
                    sectionId=section.id,
                    sectionTitle=section.title,
                    source="deterministic",
                )
            )
        if report.percent_time_flags:
            findings.append(
                AdversarialAuditFinding(
                    severity="critical",
                    category="consistency",
                    code="deterministic.integrity.percent_time",
                    message="Invented percent-time / FTE figures must remain [VERIFY: percent time].",
                    sectionId=section.id,
                    sectionTitle=section.title,
                    source="deterministic",
                )
            )
    return findings


def _collect_actionable_findings(
    audit: ProposalAdversarialAudit,
    draft: ProposalDraft,
) -> list[AdversarialAuditFinding]:
    return _dedupe_actionable_findings(
        [
            *[finding for finding in audit.findings if finding.severity == "critical"],
            *_deterministic_fixable_findings(draft),
        ]
    )


def _ensure_open_pricing_section(draft: ProposalDraft) -> tuple[ProposalDraft, str]:
    existing = next(
        (section for section in draft.sections if section.id == _OPEN_PRICING_HANDOFFS_ID),
        None,
    )
    if existing:
        return draft, existing.id
    section = ProposalSection(
        id=_OPEN_PRICING_HANDOFFS_ID,
        title="Open pricing handoffs",
        content="Unresolved pricing / budget findings require human confirmation before submission.",
        status="generated",
        source="generated",
        mode="write",
    )
    logger.info(
        "adversarial_repair created open pricing handoffs section rfp_id=%s",
        draft.rfp_id,
    )
    return draft.model_copy(
        update={
            "sections": [*draft.sections, section],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ), section.id


def _resolve_escalation_section_id(
    draft: ProposalDraft,
    *,
    section_id: str | None,
) -> tuple[ProposalDraft, str]:
    """Pick a real section for MANUAL FILL when the finding has no sectionId."""
    if section_id and any(section.id == section_id for section in draft.sections):
        return draft, section_id

    budget_idx = find_budget_section_index(draft.sections)
    if budget_idx is not None:
        return draft, draft.sections[budget_idx].id

    for section in draft.sections:
        if (section.content or "").strip():
            return draft, section.id

    return _ensure_open_pricing_section(draft)


def _append_manual_fill(
    draft: ProposalDraft,
    *,
    section_id: str | None,
    issue: str,
) -> tuple[ProposalDraft, str | None]:
    draft, target_id = _resolve_escalation_section_id(draft, section_id=section_id)
    sections = []
    appended: str | None = None
    owner = _owner_for_field(issue)
    tag = f"[MANUAL FILL: {owner} — {issue[:100].strip()}]"
    for section in draft.sections:
        if section.id != target_id:
            sections.append(section)
            continue
        if tag.casefold() not in (section.content or "").casefold():
            body = (section.content or "").rstrip()
            content = f"{body}\n\n{tag}" if body else tag
            section = section.model_copy(update={"content": content})
            appended = tag
        sections.append(section)
    if appended is None:
        return draft, None
    logger.info(
        "adversarial_repair escalated MANUAL FILL section_id=%s tag=%r",
        target_id,
        tag[:120],
    )
    return draft.model_copy(
        update={
            "sections": sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ), appended


def _report_issue_text(message: str) -> str:
    cleaned = VERIFY_TAG_RE.sub("", message or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:")
    return cleaned or "confirm before submission"


def _manuscript_has_manual_fill_for_issue(draft: ProposalDraft, issue: str) -> bool:
    needle = issue[:80].strip().casefold()
    if not needle:
        return False
    for section in draft.sections:
        for tag in extract_manual_fill_tags(section.content or ""):
            if needle in tag.description.casefold() or needle in tag.text.casefold():
                return True
    return False


def _verify_covers_finding(section: ProposalSection | None, finding: AdversarialAuditFinding) -> bool:
    """True when an existing VERIFY tag already hands off this defect family."""
    if section is None:
        return False
    content = (section.content or "").casefold()
    if "[verify:" not in content:
        return False
    family = _finding_family(finding)
    if family == "staffing_hours":
        return "staffing hours" in content
    if family == "percent_time":
        return "percent time" in content
    return False


def _finding_already_handed_off(
    draft: ProposalDraft,
    finding: AdversarialAuditFinding,
    *,
    issue_text: str,
) -> bool:
    """Only skip escalation when THIS finding already has a handoff — not any sibling tag."""
    if _manuscript_has_manual_fill_for_issue(draft, issue_text):
        return True
    section = _section_map(draft).get(finding.section_id or "")
    return _verify_covers_finding(section, finding)


def _escalate_remaining_findings(
    draft: ProposalDraft,
    findings: list[AdversarialAuditFinding],
    escalations: list[str],
) -> tuple[ProposalDraft, list[str]]:
    """Write MANUAL FILL for every remaining critical finding that lacks a handoff tag.

    Sibling defects in the same section must each get their own MANUAL FILL — an
    existing MANUAL FILL / VERIFY for a *different* issue must not suppress escalation.
    """
    working = draft
    for finding in findings:
        issue_text = _report_issue_text(finding.message)
        if _finding_already_handed_off(working, finding, issue_text=issue_text):
            continue
        working, tag = _append_manual_fill(
            working,
            section_id=finding.section_id,
            issue=issue_text,
        )
        if tag and tag not in escalations:
            escalations.append(tag)
    return working, escalations


def _attempt_entry(
    *,
    finding_code: str,
    section_id: str | None,
    strategy: str,
    outcome: str,
    attempts: int,
) -> AdversarialRepairAttempt:
    return AdversarialRepairAttempt(
        findingCode=finding_code,
        sectionId=section_id,
        strategy=strategy,
        outcome=outcome,
        attempts=attempts,
    )


async def run_adversarial_repair_loop(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    use_llm_audit: bool = True,
    use_llm_repair: bool = False,
    max_rounds: int | None = None,
    max_attempts_per_finding: int | None = None,
    time_budget_sec: int | None = None,
) -> tuple[ProposalDraft, ProposalResearchCache, ProposalAdversarialAudit, AdversarialRepairReport]:
    """Repair only fixable audit findings; escalate stubborn defects to MANUAL FILL."""
    del use_llm_repair  # LLM section repair is intentionally deferred until FP review.

    rounds_cap = max_rounds or settings.adversarial_repair_max_rounds
    attempts_cap = (
        max_attempts_per_finding or settings.adversarial_repair_max_attempts_per_finding
    )
    time_cap = time_budget_sec or settings.adversarial_repair_time_budget_sec
    started = time.monotonic()
    working_draft = draft
    working_research = research or ProposalResearchCache(
        rfpId=rfp.id,
        updatedAt=datetime.now(timezone.utc).isoformat(),
    )
    attempts_by_finding: Counter[str] = Counter()
    report_attempts: list[AdversarialRepairAttempt] = []
    escalations: list[str] = []
    stopped_reason = "max_rounds"
    rounds_run = 0
    exhausted_any = False

    logger.info(
        "adversarial_repair start rfp_id=%s max_rounds=%s attempts_cap=%s time_budget_sec=%s",
        rfp.id,
        rounds_cap,
        attempts_cap,
        time_cap,
    )

    final_audit = await run_manuscript_auditor(
        draft=working_draft,
        research=working_research,
        rfp=rfp,
        use_llm=use_llm_audit,
    )
    working_research = persist_manuscript_audit(working_research, final_audit)

    for round_index in range(1, rounds_cap + 1):
        rounds_run = round_index
        if time.monotonic() - started >= time_cap:
            stopped_reason = "time_budget_exceeded"
            break

        actionable = _collect_actionable_findings(final_audit, working_draft)
        if not actionable:
            stopped_reason = "resolved"
            break

        before = _section_map(working_draft)
        changed = False
        for finding in actionable:
            key = _finding_key(finding)
            section = before.get(finding.section_id or "")
            issue_text = _report_issue_text(finding.message)

            if _protected_finding(section, finding.message):
                # Section may already have VERIFY/MANUAL FILL for a sibling defect —
                # still escalate THIS finding unless it already has its own handoff.
                tag = None
                if not _finding_already_handed_off(
                    working_draft, finding, issue_text=issue_text
                ):
                    working_draft, tag = _append_manual_fill(
                        working_draft,
                        section_id=finding.section_id,
                        issue=issue_text,
                    )
                    if tag:
                        escalations.append(tag)
                        changed = True
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy="protected_skip",
                        outcome="manual_fill_escalated" if tag else "protected_blocked",
                        attempts=attempts_by_finding[key],
                    )
                )
                continue

            attempts_by_finding[key] += 1
            candidate = _deterministic_repair(section) if section else section
            if (
                section
                and candidate
                and candidate.content != section.content
                and manual_fill_tags_preserved(section.content or "", candidate.content or "")
                and all(
                    tag in (candidate.content or "")
                    for tag in _VERIFY_TEXT_RE.findall(section.content or "")
                )
            ):
                working_draft = working_draft.model_copy(
                    update={
                        "sections": [
                            candidate if existing.id == candidate.id else existing
                            for existing in working_draft.sections
                        ],
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                changed = True
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy="deterministic_gate",
                        outcome="patched",
                        attempts=attempts_by_finding[key],
                    )
                )
                continue

            if attempts_by_finding[key] >= attempts_cap:
                exhausted_any = True
                working_draft, tag = _append_manual_fill(
                    working_draft,
                    section_id=finding.section_id,
                    issue=issue_text,
                )
                if tag:
                    escalations.append(tag)
                    changed = True
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy="deterministic_gate",
                        outcome="manual_fill_escalated" if tag else "attempts_exhausted",
                        attempts=attempts_by_finding[key],
                    )
                )
                continue

            report_attempts.append(
                _attempt_entry(
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    strategy="deterministic_gate",
                    outcome="no_change",
                    attempts=attempts_by_finding[key],
                )
            )

        final_audit = await run_manuscript_auditor(
            draft=working_draft,
            research=working_research,
            rfp=rfp,
            use_llm=use_llm_audit,
        )
        working_research = persist_manuscript_audit(working_research, final_audit)

        if escalations:
            stopped_reason = "manual_fill_required"
            break
        if not changed:
            stopped_reason = "attempts_exhausted" if exhausted_any else "no_progress"
            break

    remaining = _collect_actionable_findings(final_audit, working_draft)
    if stopped_reason in _NON_CONVERGENCE_REASONS and remaining:
        working_draft, escalations = _escalate_remaining_findings(
            working_draft,
            remaining,
            escalations,
        )
        # Re-audit after escalation so persisted findings match manuscript tags.
        final_audit = await run_manuscript_auditor(
            draft=working_draft,
            research=working_research,
            rfp=rfp,
            use_llm=use_llm_audit,
        )
        working_research = persist_manuscript_audit(working_research, final_audit)
        if escalations and stopped_reason in {
            "no_progress",
            "time_budget_exceeded",
            "max_rounds",
            "attempts_exhausted",
        }:
            # Keep original stop reason for diagnostics; escalations list records tags.
            logger.info(
                "adversarial_repair escalated remaining criticals rfp_id=%s reason=%s count=%s",
                rfp.id,
                stopped_reason,
                len(escalations),
            )

    working_research = working_research.model_copy(
        update={
            "adversarial_repair_report": AdversarialRepairReport(
                roundsRun=rounds_run,
                stoppedReason=stopped_reason,
                resolved=stopped_reason == "resolved" and not escalations,
                attempts=report_attempts,
                escalations=escalations,
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.info(
        "adversarial_repair finished rfp_id=%s rounds=%s stopped_reason=%s escalations=%s critical_remaining=%s",
        rfp.id,
        rounds_run,
        stopped_reason,
        len(escalations),
        sum(1 for finding in final_audit.findings if finding.severity == "critical"),
    )
    return (
        working_draft,
        working_research,
        final_audit,
        working_research.adversarial_repair_report,
    )


def adversarial_repair_blocking_issues(
    report: AdversarialRepairReport | None,
) -> list[PreSubmitIssue]:
    """Surface unresolved adversarial repair work as a blocking pre-submit issue."""
    if report is None or report.resolved:
        return []
    return [
        PreSubmitIssue(
            severity="critical",
            category="adversarial_repair",
            message=(
                "Adversarial repair loop did not fully converge; review MANUAL FILL handoff "
                f"and unresolved manuscript findings ({report.stopped_reason})."
            ),
        )
    ]
