"""Bounded validate -> repair -> re-validate loop for manuscript audit findings."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

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
    RepairPlan,
)
from app.models.rfp import RfpRecord
from app.services.evidence_trust.legal_attestation_gate import (
    gate_section_legal_attestations,
)
from app.services.proposal_adversarial_repair_planner import build_repair_plan
from app.services.proposal_adversarial_repair_verifier import verify_repair_attempt
from app.services.proposal_evidence_gate import decide_evidence_action
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
from app.services.proposal_repository import asave_proposal_draft, asave_research_cache
from app.services.proposal_section_editor import improve_proposal_section

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
    from app.services.proposal_cert_claim_scrub import scrub_section_cert_claims

    repaired, _cert_logs = scrub_section_cert_claims(repaired)
    return repaired


def _apply_deterministic_patch(
    working_draft: ProposalDraft, section: ProposalSection | None
) -> tuple[ProposalDraft, bool]:
    """Apply legal-attestation + cert scrub to `section` if content changes safely.

    Returns the (possibly updated) draft and whether a patch was applied. A patch is
    only accepted when it preserves any existing MANUAL FILL / VERIFY handoffs —
    the gate must never silently drop an unresolved factual gap.
    """
    if not section:
        return working_draft, False
    candidate = _deterministic_repair(section)
    if (
        not candidate
        or candidate.content == section.content
        or not manual_fill_tags_preserved(section.content or "", candidate.content or "")
        or not all(
            tag in (candidate.content or "")
            for tag in _VERIFY_TEXT_RE.findall(section.content or "")
        )
    ):
        return working_draft, False
    updated_draft = working_draft.model_copy(
        update={
            "sections": [
                candidate if existing.id == candidate.id else existing
                for existing in working_draft.sections
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return updated_draft, True


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


def _finding_priority(finding: AdversarialAuditFinding) -> int:
    """Lower = repair sooner (cheaper/higher leverage families first)."""
    blob = f"{finding.code or ''} {finding.category or ''} {finding.message or ''}".casefold()
    if "budget" in blob or "money" in blob or "free_currency" in blob:
        return 0
    if "placeholder" in blob or "verify" in blob:
        return 1
    if "truncation" in blob or "note_leak" in blob:
        return 2
    if "compliance" in blob:
        return 3
    if "fabrication" in blob:
        return 4
    if "inconsistency" in blob or blob.startswith("llm."):
        return 8
    return 5


def _triage_actionable_findings(
    findings: list[AdversarialAuditFinding],
    *,
    max_findings: int,
) -> list[AdversarialAuditFinding]:
    ordered = sorted(findings, key=_finding_priority)
    if max_findings <= 0 or len(ordered) <= max_findings:
        return ordered
    return ordered[:max_findings]


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
    finding_code: str | None = None,
) -> tuple[ProposalDraft, str | None]:
    """Append a Sonja handoff tag. Prefer stable finding codes over truncated prose."""
    draft, target_id = _resolve_escalation_section_id(draft, section_id=section_id)
    sections = []
    appended: str | None = None
    owner = _owner_for_field(issue)
    code = (finding_code or "").strip()
    short_title = _report_issue_text(issue)
    if len(short_title) > 80:
        short_title = short_title[:77].rstrip() + "…"
    if code:
        tag = f"[MANUAL FILL: {owner} — {code}" + (
            f" | {short_title}]" if short_title and short_title.casefold() not in code.casefold() else "]"
        )
    else:
        # Fallback: full cleaned issue (no mid-word [:100] cut).
        tag = f"[MANUAL FILL: {owner} — {short_title}]"
    for section in draft.sections:
        if section.id != target_id:
            sections.append(section)
            continue
        existing = section.content or ""
        already = tag.casefold() in existing.casefold()
        if code and f"— {code}".casefold() in existing.casefold():
            already = True
        if not already:
            body = existing.rstrip()
            content = f"{body}\n\n{tag}" if body else tag
            section = section.model_copy(update={"content": content})
            appended = tag
        sections.append(section)
    if appended is None:
        return draft, None
    logger.info(
        "adversarial_repair escalated MANUAL FILL section_id=%s tag=%r",
        target_id,
        tag[:160],
    )
    return draft.model_copy(
        update={
            "sections": sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ), appended


def ensure_open_pricing_handoffs_section(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, str]:
    """Public alias for pricing-sync / other callers."""
    return _ensure_open_pricing_section(draft)


def append_manual_fill_tag(
    draft: ProposalDraft,
    *,
    section_id: str | None,
    issue: str,
    finding_code: str | None = None,
) -> tuple[ProposalDraft, str | None]:
    """Public alias for appending a Sonja MANUAL FILL handoff tag."""
    return _append_manual_fill(
        draft,
        section_id=section_id,
        issue=issue,
        finding_code=finding_code,
    )


def _report_issue_text(message: str) -> str:
    cleaned = VERIFY_TAG_RE.sub("", message or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:")
    return cleaned or "confirm before submission"


def _manuscript_has_manual_fill_for_issue(
    draft: ProposalDraft,
    issue: str,
    *,
    finding_code: str | None = None,
) -> bool:
    code = (finding_code or "").strip().casefold()
    needle = _report_issue_text(issue)[:80].strip().casefold()
    if not needle and not code:
        return False
    for section in draft.sections:
        for tag in extract_manual_fill_tags(section.content or ""):
            blob = f"{tag.description} {tag.text}".casefold()
            if code and code in blob:
                return True
            if needle and (needle in tag.description.casefold() or needle in tag.text.casefold()):
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
    if _manuscript_has_manual_fill_for_issue(
        draft, issue_text, finding_code=finding.code
    ):
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
    Full finding messages are kept in the repair report; tags use stable codes.
    """
    working = draft
    for finding in findings:
        issue_text = _report_issue_text(finding.message)
        if _finding_already_handed_off(working, finding, issue_text=issue_text):
            continue
        # Persist full message in report list even when tag uses code.
        if issue_text and issue_text not in escalations:
            escalations.append(issue_text)
        working, tag = _append_manual_fill(
            working,
            section_id=finding.section_id,
            issue=issue_text,
            finding_code=finding.code,
        )
        if tag and tag not in escalations:
            escalations.append(tag)
    return working, escalations


def _build_repair_message_for_finding(
    *,
    finding: AdversarialAuditFinding,
    repair_plan: RepairPlan,
    failure_reason: str | None,
    prior_attempt_summary: str,
    use_strong_model: bool,
) -> str:
    """Compose a failure-aware improve prompt for one adversarial finding."""
    parts = [
        finding.message.strip(),
        f"Previous attempt failed because: {failure_reason or 'none'}",
        f"Previous outcome: {prior_attempt_summary or 'none'}",
        f"Repair mode: {repair_plan.repair_mode}",
        (
            "Rules: reduce broad VERIFY blocks where safely possible; never invent "
            "fact-bound claims; keep unresolved factual gaps narrow and explicit."
        ),
    ]
    if repair_plan.safe_plan_driven_draft:
        parts.append(
            "Methodology and process content may be drafted from the RFP requirements "
            "and execution plan without inventing company-specific facts."
        )
    if use_strong_model or repair_plan.needs_strong_model:
        parts.append(
            "This is a strong-model escalation pass — prioritize accurate, evidence-grounded "
            "repairs over cosmetic rewrites."
        )
    return "\n\n".join(part for part in parts if part)


async def repair_section_for_finding(
    *,
    rfp_id: str,
    section_id: str,
    finding: AdversarialAuditFinding,
    repair_plan: RepairPlan,
    failure_reason: str | None,
    prior_attempt_summary: str,
    use_strong_model: bool = False,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache | None, str]:
    """Run a targeted section improve pass for one adversarial audit finding."""
    if not (section_id or "").strip():
        raise ValueError("section_id is required for targeted section repair")

    repair_message = _build_repair_message_for_finding(
        finding=finding,
        repair_plan=repair_plan,
        failure_reason=failure_reason,
        prior_attempt_summary=prior_attempt_summary,
        use_strong_model=use_strong_model,
    )
    logger.info(
        "adversarial_repair section repair rfp_id=%s section_id=%s finding_code=%s "
        "repair_mode=%s failure_reason=%s use_strong_model=%s",
        rfp_id,
        section_id,
        finding.code,
        repair_plan.repair_mode,
        failure_reason or "none",
        use_strong_model or repair_plan.needs_strong_model,
    )
    section, draft, research, provider, _detail, _changed = await improve_proposal_section(
        rfp_id,
        section_id,
        repair_message,
        persist=True,
    )
    return section, draft, research, provider


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
    use_llm_repair: bool = True,
    max_rounds: int | None = None,
    max_attempts_per_finding: int | None = None,
    time_budget_sec: int | None = None,
) -> tuple[ProposalDraft, ProposalResearchCache, ProposalAdversarialAudit, AdversarialRepairReport]:
    """Repair fixable audit findings; escalate stubborn defects to MANUAL FILL.

    When `use_llm_repair` is False, only the deterministic legal-attestation gate
    runs and unresolved findings escalate straight to MANUAL FILL (legacy behavior
    relied on by existing regression tests). When True, each finding/attempt is
    routed through a `RepairPlan` from `build_repair_plan` — deterministic patch,
    targeted section rewrite, or budget handoff — and section rewrites are checked
    with `verify_repair_attempt` so failure reasons carry forward into the next
    attempt's plan.
    """
    rounds_cap = max_rounds or settings.adversarial_repair_max_rounds
    attempts_cap = (
        max_attempts_per_finding or settings.adversarial_repair_max_attempts_per_finding
    )
    time_cap = time_budget_sec or settings.adversarial_repair_time_budget_sec
    findings_cap = max(1, int(settings.adversarial_repair_max_findings_per_round or 12))
    llm_each_round = bool(settings.adversarial_repair_llm_audit_each_round)
    started = time.monotonic()
    working_draft = draft
    working_research = research or ProposalResearchCache(
        rfpId=rfp.id,
        updatedAt=datetime.now(timezone.utc).isoformat(),
    )
    attempts_by_finding: Counter[str] = Counter()
    prior_by_key: dict[str, SimpleNamespace] = {}
    report_attempts: list[AdversarialRepairAttempt] = []
    escalations: list[str] = []
    stopped_reason = "max_rounds"
    rounds_run = 0
    exhausted_any = False

    logger.info(
        "adversarial_repair start rfp_id=%s max_rounds=%s attempts_cap=%s time_budget_sec=%s "
        "findings_cap=%s use_llm_repair=%s llm_audit_each_round=%s",
        rfp.id,
        rounds_cap,
        attempts_cap,
        time_cap,
        findings_cap,
        use_llm_repair,
        llm_each_round,
    )
    from app.core.step_debug_logger import step_trace, summarize_sections

    step_trace(
        "adversarial_repair_start",
        rfp_id=rfp.id,
        max_rounds=rounds_cap,
        attempts_cap=attempts_cap,
        time_budget_sec=time_cap,
        findings_cap=findings_cap,
        use_llm_audit=use_llm_audit,
        use_llm_repair=use_llm_repair,
        **summarize_sections(working_draft.sections),
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

        actionable = _triage_actionable_findings(
            _collect_actionable_findings(final_audit, working_draft),
            max_findings=findings_cap,
        )
        if not actionable:
            stopped_reason = "resolved"
            break

        step_trace(
            "adversarial_repair_round_triage",
            rfp_id=rfp.id,
            round_index=round_index,
            actionable=len(actionable),
            findings_cap=findings_cap,
        )

        changed = False
        pending_section_repairs: list[dict] = []
        rewritten_sections: set[str] = set()

        for finding in actionable:
            key = _finding_key(finding)
            # Re-read from working_draft on every iteration (not a round-start snapshot)
            # so a mutation from an earlier finding in this round — e.g. a deterministic
            # patch or section rewrite touching the same section_id — is visible before
            # we capture before_section / apply the next patch for this finding.
            section = _section_map(working_draft).get(finding.section_id or "")
            issue_text = _report_issue_text(finding.message)

            # Bio stub + PDF designer-note sections are complete for Option B —
            # do not rewrite or append Key Accounts MANUAL FILL tags.
            try:
                from app.services.proposal_bio_stub import is_bio_stub_section

                if section is not None and is_bio_stub_section(
                    section.id, section.content or ""
                ):
                    report_attempts.append(
                        _attempt_entry(
                            finding_code=finding.code,
                            section_id=finding.section_id,
                            strategy="bio_stub_skip",
                            outcome="bio_stub_protected",
                            attempts=attempts_by_finding[key],
                        )
                    )
                    continue
            except Exception:
                pass

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
                        finding_code=finding.code,
                    )
                    if tag:
                        escalations.append(tag)
                        changed = True
                outcome = "manual_fill_escalated" if tag else "protected_blocked"
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy="protected_skip",
                        outcome=outcome,
                        attempts=attempts_by_finding[key],
                    )
                )
                step_trace(
                    "adversarial_repair_attempt",
                    rfp_id=rfp.id,
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    attempt_number=attempts_by_finding[key],
                    repair_mode="protected_skip",
                    failure_reason="protected_section",
                    outcome=outcome,
                    resolved=False,
                    improved=False,
                )
                continue

            attempts_by_finding[key] += 1
            attempt_number = attempts_by_finding[key]

            if not use_llm_repair:
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
                            attempts=attempt_number,
                        )
                    )
                    continue

                if attempt_number >= attempts_cap:
                    exhausted_any = True
                    working_draft, tag = _append_manual_fill(
                        working_draft,
                        section_id=finding.section_id,
                        issue=issue_text,
                        finding_code=finding.code,
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
                            attempts=attempt_number,
                        )
                    )
                    continue

                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy="deterministic_gate",
                        outcome="no_change",
                        attempts=attempt_number,
                    )
                )
                continue

            # ---- planner-driven intelligent repair ----
            prior = prior_by_key.get(key)
            plan = build_repair_plan(
                finding=finding,
                attempt_number=attempt_number,
                previous_outcome=(prior.outcome if prior else ""),
                failure_reason=(prior.failure_reason if prior else None),
            )

            if plan.repair_mode == "budget_canonical_repair":
                # Never freeform-rewrite budget/pricing content — deterministic
                # cleanup only, otherwise hand off for human/pricing reconciliation.
                working_draft, patched = _apply_deterministic_patch(working_draft, section)
                if patched:
                    changed = True
                    outcome = "patched"
                    prior_by_key.pop(key, None)
                else:
                    working_draft, tag = _append_manual_fill(
                        working_draft,
                        section_id=finding.section_id,
                        issue=issue_text,
                        finding_code=finding.code,
                    )
                    if tag:
                        escalations.append(tag)
                        changed = True
                    outcome = "manual_fill_escalated" if tag else "budget_handoff_pending"
                    prior_by_key[key] = SimpleNamespace(
                        outcome=outcome, failure_reason="budget_conflict"
                    )
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy=plan.repair_mode,
                        outcome=outcome,
                        attempts=attempt_number,
                    )
                )
                step_trace(
                    "adversarial_repair_attempt",
                    rfp_id=rfp.id,
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    attempt_number=attempt_number,
                    repair_mode=plan.repair_mode,
                    failure_reason=plan.failure_reason,
                    outcome=outcome,
                    resolved=False,
                    improved=patched,
                )
                continue

            if plan.repair_mode in {"protected_skip", "manual_fill"}:
                working_draft, tag = _append_manual_fill(
                    working_draft, section_id=finding.section_id, issue=issue_text,
                    finding_code=finding.code,
                )
                if tag:
                    escalations.append(tag)
                    changed = True
                outcome = "manual_fill_escalated" if tag else "protected_blocked"
                prior_by_key[key] = SimpleNamespace(
                    outcome=outcome, failure_reason="protected_section"
                )
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy=plan.repair_mode,
                        outcome=outcome,
                        attempts=attempt_number,
                    )
                )
                step_trace(
                    "adversarial_repair_attempt",
                    rfp_id=rfp.id,
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    attempt_number=attempt_number,
                    repair_mode=plan.repair_mode,
                    failure_reason="protected_section",
                    outcome=outcome,
                    resolved=False,
                    improved=False,
                    **decide_evidence_action(
                        section_id=finding.section_id,
                        section_title=finding.section_title,
                        finding=finding,
                    ).as_log_dict(),
                )
                continue

            if plan.repair_mode == "deterministic_cleanup":
                working_draft, patched = _apply_deterministic_patch(working_draft, section)
                if patched:
                    changed = True
                    outcome = "patched"
                    prior_by_key.pop(key, None)
                elif attempt_number >= attempts_cap:
                    exhausted_any = True
                    working_draft, tag = _append_manual_fill(
                        working_draft,
                        section_id=finding.section_id,
                        issue=issue_text,
                        finding_code=finding.code,
                    )
                    if tag:
                        escalations.append(tag)
                        changed = True
                    outcome = "manual_fill_escalated" if tag else "attempts_exhausted"
                    prior_by_key[key] = SimpleNamespace(
                        outcome=outcome, failure_reason="no_change"
                    )
                else:
                    outcome = "no_change"
                    prior_by_key[key] = SimpleNamespace(
                        outcome=outcome, failure_reason="no_change"
                    )
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy=plan.repair_mode,
                        outcome=outcome,
                        attempts=attempt_number,
                    )
                )
                step_trace(
                    "adversarial_repair_attempt",
                    rfp_id=rfp.id,
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    attempt_number=attempt_number,
                    repair_mode=plan.repair_mode,
                    failure_reason=plan.failure_reason,
                    outcome=outcome,
                    resolved=False,
                    improved=patched,
                )
                continue

            is_deterministic_family = (
                _finding_family(finding) in {"staffing_hours", "percent_time"}
                or (finding.code or "").startswith("deterministic.integrity.")
            )
            if is_deterministic_family:
                working_draft, patched = _apply_deterministic_patch(working_draft, section)
                if patched:
                    changed = True
                    outcome = "patched"
                    prior_by_key.pop(key, None)
                elif attempt_number >= attempts_cap:
                    exhausted_any = True
                    working_draft, tag = _append_manual_fill(
                        working_draft,
                        section_id=finding.section_id,
                        issue=issue_text,
                        finding_code=finding.code,
                    )
                    if tag:
                        escalations.append(tag)
                        changed = True
                    outcome = "manual_fill_escalated" if tag else "attempts_exhausted"
                    prior_by_key[key] = SimpleNamespace(outcome=outcome, failure_reason="no_change")
                else:
                    outcome = "no_change"
                    prior_by_key[key] = SimpleNamespace(outcome=outcome, failure_reason="no_change")
                report_attempts.append(
                    _attempt_entry(
                        finding_code=finding.code,
                        section_id=finding.section_id,
                        strategy=plan.repair_mode,
                        outcome=outcome,
                        attempts=attempt_number,
                    )
                )
                step_trace(
                    "adversarial_repair_attempt",
                    rfp_id=rfp.id,
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    attempt_number=attempt_number,
                    repair_mode=plan.repair_mode,
                    failure_reason=plan.failure_reason,
                    outcome=outcome,
                    resolved=False,
                    improved=patched,
                )
                continue

            if finding.section_id:
                sid = finding.section_id
                # One LLM rewrite per section per round — later findings on the same
                # section wait for the next round / re-audit.
                if sid in rewritten_sections:
                    report_attempts.append(
                        _attempt_entry(
                            finding_code=finding.code,
                            section_id=sid,
                            strategy="section_batched_skip",
                            outcome="deferred_same_section",
                            attempts=attempt_number,
                        )
                    )
                    step_trace(
                        "adversarial_repair_attempt",
                        rfp_id=rfp.id,
                        finding_code=finding.code,
                        section_id=sid,
                        attempt_number=attempt_number,
                        repair_mode="section_batched_skip",
                        outcome="deferred_same_section",
                        resolved=False,
                        improved=False,
                    )
                    # Don't burn an attempt for deferred siblings
                    attempts_by_finding[key] = max(0, attempts_by_finding[key] - 1)
                    continue

                before_section = section
                try:
                    await asave_proposal_draft(working_draft)
                    await asave_research_cache(working_research)
                    new_section, new_draft, new_research, _provider = (
                        await repair_section_for_finding(
                            rfp_id=rfp.id,
                            section_id=finding.section_id,
                            finding=finding,
                            repair_plan=plan,
                            failure_reason=plan.failure_reason,
                            prior_attempt_summary=plan.previous_outcome or "none",
                            use_strong_model=plan.needs_strong_model,
                        )
                    )
                    rewritten_sections.add(sid)
                except Exception as exc:  # noqa: BLE001 - keep the repair loop resilient
                    logger.warning(
                        "adversarial_repair section repair raised rfp_id=%s section_id=%s "
                        "finding_code=%s attempt=%s error=%s",
                        rfp.id,
                        finding.section_id,
                        finding.code,
                        attempt_number,
                        exc,
                    )
                else:
                    working_draft = new_draft
                    if new_research is not None:
                        working_research = new_research
                    after_section = _section_map(working_draft).get(finding.section_id)
                    if (
                        after_section
                        and before_section
                        and after_section.content != before_section.content
                    ):
                        changed = True

                pending_section_repairs.append(
                    {
                        "finding": finding,
                        "key": key,
                        "attempt_number": attempt_number,
                        "plan": plan,
                        "before_section": before_section,
                    }
                )
                continue

            # No section to target — hand off directly, mirroring legacy behavior.
            working_draft, tag = _append_manual_fill(
                working_draft,
                section_id=None,
                issue=issue_text,
                finding_code=finding.code,
            )
            if tag:
                escalations.append(tag)
                changed = True
            outcome = "manual_fill_escalated" if tag else "no_section"
            prior_by_key[key] = SimpleNamespace(outcome=outcome, failure_reason=None)
            report_attempts.append(
                _attempt_entry(
                    finding_code=finding.code,
                    section_id=finding.section_id,
                    strategy=plan.repair_mode,
                    outcome=outcome,
                    attempts=attempt_number,
                )
            )
            step_trace(
                "adversarial_repair_attempt",
                rfp_id=rfp.id,
                finding_code=finding.code,
                section_id=finding.section_id,
                attempt_number=attempt_number,
                repair_mode=plan.repair_mode,
                failure_reason=plan.failure_reason,
                outcome=outcome,
                resolved=False,
                improved=False,
            )

        # Middle rounds: deterministic-only re-audit unless configured otherwise.
        round_use_llm = bool(
            use_llm_audit
            and (llm_each_round or round_index >= rounds_cap or round_index == 1)
        )
        final_audit = await run_manuscript_auditor(
            draft=working_draft,
            research=working_research,
            rfp=rfp,
            use_llm=round_use_llm,
        )
        working_research = persist_manuscript_audit(working_research, final_audit)

        if pending_section_repairs:
            # Verify targeted section rewrites against the fresh round audit rather
            # than re-auditing per finding — cheaper, and matches the once-per-round
            # cadence the deterministic path already uses.
            remaining_for_verification = _collect_actionable_findings(final_audit, working_draft)
            current_sections = _section_map(working_draft)
            for item in pending_section_repairs:
                pfinding: AdversarialAuditFinding = item["finding"]
                pkey: str = item["key"]
                pattempt: int = item["attempt_number"]
                pplan: RepairPlan = item["plan"]
                pbefore_section: ProposalSection | None = item["before_section"]
                pafter_section = current_sections.get(pfinding.section_id or "")
                new_findings_for_section = [
                    f
                    for f in remaining_for_verification
                    if (f.section_id or "") == (pfinding.section_id or "")
                ]
                verification = verify_repair_attempt(
                    finding=pfinding,
                    before=pbefore_section,
                    after=pafter_section,
                    new_findings=new_findings_for_section,
                )

                if verification.outcome == "resolved":
                    prior_by_key.pop(pkey, None)
                else:
                    if verification.introduced_critical:
                        p_failure_reason = "introduced_new_finding"
                    elif verification.outcome == "improved_but_unresolved":
                        p_failure_reason = (
                            "evidence_missing"
                            if pplan.requires_targeted_retrieval
                            else "still_unverified"
                        )
                    else:
                        p_failure_reason = "no_change"
                    prior_by_key[pkey] = SimpleNamespace(
                        outcome=verification.outcome, failure_reason=p_failure_reason
                    )

                outcome_label = verification.outcome
                if pattempt >= attempts_cap and not verification.resolved:
                    exhausted_any = True
                    working_draft, tag = _append_manual_fill(
                        working_draft,
                        section_id=pfinding.section_id,
                        issue=_report_issue_text(pfinding.message),
                        finding_code=pfinding.code,
                    )
                    if tag:
                        escalations.append(tag)
                        changed = True
                        outcome_label = "manual_fill_escalated"

                report_attempts.append(
                    _attempt_entry(
                        finding_code=pfinding.code,
                        section_id=pfinding.section_id,
                        strategy=pplan.repair_mode,
                        outcome=outcome_label,
                        attempts=pattempt,
                    )
                )
                step_trace(
                    "adversarial_repair_attempt",
                    rfp_id=rfp.id,
                    finding_code=pfinding.code,
                    section_id=pfinding.section_id,
                    attempt_number=pattempt,
                    repair_mode=pplan.repair_mode,
                    failure_reason=pplan.failure_reason,
                    outcome=outcome_label,
                    resolved=verification.resolved,
                    improved=verification.improved,
                )

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

    by_outcome: Counter[str] = Counter()
    fixed_codes: list[str] = []
    escalated_codes: list[str] = []
    skipped_codes: list[str] = []
    for attempt in report_attempts:
        outcome = str(getattr(attempt, "outcome", "") or "")
        code = str(getattr(attempt, "finding_code", "") or "")
        by_outcome[outcome or "unknown"] += 1
        if outcome in {"resolved", "patched"}:
            if code and code not in fixed_codes:
                fixed_codes.append(code)
        elif outcome in {
            "manual_fill_escalated",
            "protected_blocked",
            "bio_stub_protected",
            "budget_handoff_pending",
        }:
            if code and code not in escalated_codes:
                escalated_codes.append(code)
        elif outcome and code:
            # Keep a short sample of other non-success outcomes for debug.
            sample = f"{code}:{outcome}"
            if sample not in skipped_codes and len(skipped_codes) < 24:
                skipped_codes.append(sample)

    outcome_summary = {
        "byOutcome": dict(by_outcome),
        "fixedCodes": fixed_codes[:40],
        "escalatedCodes": escalated_codes[:40],
        "otherOutcomes": skipped_codes[:24],
        "attemptCount": len(report_attempts),
        "escalationTagCount": len(escalations),
        "escalationSamples": [str(tag)[:180] for tag in escalations[:8]],
    }
    working_research = working_research.model_copy(
        update={
            "adversarial_repair_report": AdversarialRepairReport(
                roundsRun=rounds_run,
                stoppedReason=stopped_reason,
                resolved=stopped_reason == "resolved" and not escalations,
                attempts=report_attempts,
                escalations=escalations,
                outcomeSummary=outcome_summary,
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.info(
        "adversarial_repair finished rfp_id=%s rounds=%s stopped_reason=%s "
        "escalations=%s critical_remaining=%s by_outcome=%s fixed=%s escalated_codes=%s",
        rfp.id,
        rounds_run,
        stopped_reason,
        len(escalations),
        sum(1 for finding in final_audit.findings if finding.severity == "critical"),
        dict(by_outcome),
        fixed_codes[:12],
        escalated_codes[:12],
    )
    step_trace(
        "adversarial_repair_finished",
        rfp_id=rfp.id,
        rounds_run=rounds_run,
        stopped_reason=stopped_reason,
        escalation_count=len(escalations),
        critical_remaining=sum(
            1 for finding in final_audit.findings if finding.severity == "critical"
        ),
        findings=len(final_audit.findings),
        outcome_summary=outcome_summary,
        **summarize_sections(working_draft.sections),
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
