"""Scan RFP — DQ / gov-policy gate + lightweight coverage orchestrator loop.

Evaluate → act → recheck (max 2 ledger passes). Reuses go/no-go analysis,
legal attestation gates, page-limit / altered-form signals, and the existing
ledger ADD/MERGE/CUT path. Never invents certifications or legal attestations.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import LedgerReconcileResult
from app.services.rfp_page_limit import resolve_page_limit

logger = logging.getLogger(__name__)

_ELIGIBILITY_DQ_RE = re.compile(
    r"(?i)\b(?:"
    r"set[\s-]?aside|disqualif|must\s+be\s+(?:a\s+)?"
    r"(?:certified\s+)?(?:wbe|wosb|dbe|mbe|sdvosb|hubzone)|"
    r"(?:bid|performance|payment)\s+bond|bonding\s+required|"
    r"license[d]?|licensure|registered\s+to\s+do\s+business|"
    r"mandatory\s+pre[\s-]?bid|prime\s+contractor\s+must|"
    r"in[\s-]?state\s+(?:office|presence)|physical\s+presence\s+required|"
    r"late\s+submissions?\s+(?:will\s+not|shall\s+not|not)\s+be\s+accepted|"
    r"e-?verify|"
    r"sealed\s+(?:envelope|bid|package)|"
    r"separate\s+(?:technical|cost|price)\s+(?:and|&|\/)\s+(?:cost|price|technical)|"
    r"do\s+not\s+include\s+pricing\s+in\s+(?:the\s+)?technical|"
    r"pricing\s+(?:must|shall)\s+not\s+appear\s+in\s+(?:the\s+)?technical|"
    r"non[\s-]?responsive|instant(?:ly)?\s+reject"
    r")\b"
)

_MAX_ORCHESTRATOR_PASSES = 2


@dataclass
class ScanDqGateResult:
    draft: ProposalDraft
    research: ProposalResearchCache | None
    disqualification_risks: list[str] = field(default_factory=list)
    human_decision_gaps: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    changed: bool = False


@dataclass
class ScanOrchestratorResult:
    draft: ProposalDraft
    research: ProposalResearchCache | None
    ledger_result: LedgerReconcileResult | None
    ledger_draft_logs: list[str] = field(default_factory=list)
    dq: ScanDqGateResult | None = None
    loop_passes: int = 1
    logs: list[str] = field(default_factory=list)


def _analysis_dict(rfp: RfpRecord) -> dict[str, Any]:
    raw = getattr(rfp, "go_no_go_analysis", None)
    return raw if isinstance(raw, dict) else {}


def _flag_messages(flags: Any, *, severities: set[str]) -> list[str]:
    out: list[str] = []
    if not isinstance(flags, list):
        return out
    for flag in flags:
        if not isinstance(flag, dict):
            continue
        sev = str(flag.get("severity") or "").casefold()
        if sev not in severities:
            continue
        cat = str(flag.get("category") or "compliance").strip()
        msg = str(flag.get("message") or flag.get("text") or flag.get("detail") or "").strip()
        if not msg:
            msg = cat.replace("_", " ")
        out.append(f"{cat}: {msg}" if cat else msg)
    return out


def collect_go_no_go_dq_risks(rfp: RfpRecord) -> list[str]:
    """Map persisted go/no-go into Scan risks — true DQ only, not capability gaps.

    Unverified capability / thin KB proof belongs in Go/No-Go review, not the
    Scan "disqualification" banner (those flooded Oshkosh-style scans with
    Brittany Frazier / pricing-guide evidence noise).
    """
    risks: list[str] = []
    analysis = _analysis_dict(rfp)
    recommendation = (
        str(getattr(rfp, "go_no_go", None) or analysis.get("recommendation") or "")
        .strip()
        .casefold()
    )
    if recommendation == "no_go":
        risks.append(
            "Go/No-Go recommendation is No-Go — do not treat this proposal as "
            "submission-ready until leadership clears the blockers."
        )

    deadline = analysis.get("deadline") if isinstance(analysis.get("deadline"), dict) else {}
    if deadline.get("isPast") and deadline.get("lateSubmissionDisqualifies"):
        due = deadline.get("dueDate") or "see RFP"
        risks.append(
            f"Proposal deadline passed ({due}) — late submissions are an explicit "
            "disqualifier per the RFP."
        )

    compliance = analysis.get("compliance") if isinstance(analysis.get("compliance"), dict) else {}
    # Only hard eligibility / submission / disqualification compliance flags.
    _DQ_CATEGORIES = {
        "eligibility",
        "disqualification",
        "deadline",
        "submission",
        "bonding",
        "licensing",
        "set_aside",
        "set-aside",
    }
    if isinstance(compliance.get("flags"), list):
        for flag in compliance["flags"]:
            if not isinstance(flag, dict):
                continue
            sev = str(flag.get("severity") or "").casefold()
            if sev not in {"critical", "warning"}:
                continue
            cat = str(flag.get("category") or "").strip().casefold().replace(" ", "_")
            msg = str(flag.get("message") or flag.get("text") or flag.get("detail") or "").strip()
            if not msg:
                continue
            # Skip capability / evidence adjudication noise
            if msg.casefold().startswith("unverified capability"):
                continue
            if cat and cat not in _DQ_CATEGORIES and sev != "critical":
                continue
            if cat in _DQ_CATEGORIES or (
                sev == "critical"
                and any(
                    k in msg.casefold()
                    for k in (
                        "disqualif",
                        "set-aside",
                        "set aside",
                        "must be certified",
                        "late submission",
                        "bonding required",
                    )
                )
            ):
                risks.append(f"{cat}: {msg}" if cat else msg)

    seen: set[str] = set()
    unique: list[str] = []
    for risk in risks:
        key = risk.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(risk)
    return unique


def collect_go_no_go_review_gaps(rfp: RfpRecord) -> list[str]:
    """Capability / criticalGaps for human Go/No-Go review — not DQ banner."""
    analysis = _analysis_dict(rfp)
    gaps: list[str] = []
    for gap in analysis.get("criticalGaps") or []:
        if isinstance(gap, str) and gap.strip():
            gaps.append(gap.strip())
    scope = analysis.get("scopeMatch") if isinstance(analysis.get("scopeMatch"), dict) else {}
    gaps.extend(_flag_messages(scope.get("flags"), severities={"critical", "warning"}))
    seen: set[str] = set()
    unique: list[str] = []
    for gap in gaps:
        key = gap.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(gap)
    return unique


def collect_rfp_text_dq_risks(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    rfp_text: str,
    ledger_result: LedgerReconcileResult | None,
) -> list[str]:
    """Deterministic DQ signals from RFP text + ledger advisories (no LLM)."""
    risks: list[str] = []

    from app.services.proposal_rfp_excerpt import rfp_forbids_quotation_form_changes

    if rfp_forbids_quotation_form_changes(rfp_text):
        risks.append(
            "RFP disqualifies altered quotation/pricing forms — fill the buyer's "
            "official form only; do not invent Section A–D substitutes."
        )

    text_cf = (rfp_text or "").casefold()
    if re.search(
        r"sealed\s+(?:envelope|bid|package)|original\s+signature|wet[\s-]?ink",
        text_cf,
    ):
        risks.append(
            "RFP requires sealed package and/or original wet-ink signature — "
            "confirm physical submission package before upload (disqualification if missing)."
        )
    if re.search(
        r"separate\s+(?:technical|cost|price).{0,40}(?:cost|price|technical)|"
        r"do\s+not\s+include\s+pricing\s+in\s+(?:the\s+)?technical|"
        r"pricing\s+(?:must|shall)\s+not\s+appear\s+in\s+(?:the\s+)?technical",
        text_cf,
    ):
        risks.append(
            "RFP requires separate technical/cost volumes (or forbids pricing in "
            "technical) — keep fee tables out of the technical manuscript."
        )
    if re.search(r"\be-?verify\b", text_cf):
        risks.append(
            "RFP references E-Verify — do not assert enrollment unless verified; "
            "attach required affidavit / enrollment proof with the package."
        )

    page_limit = resolve_page_limit(getattr(rfp, "page_limit", None), rfp_text)
    if page_limit and page_limit > 0:
        words = sum(len((s.content or "").split()) for s in draft.sections)
        budget = int(page_limit * 350 * 0.92)
        if words > budget:
            risks.append(
                f"Manuscript still ~{words} words vs ~{budget}-word qualification "
                f"budget ({page_limit}-page limit with headroom) — further cuts may "
                "be required before submit."
            )

    if ledger_result is not None:
        for advisory in ledger_result.advisory_submission_instructions:
            text = advisory.requirement_text or ""
            if _ELIGIBILITY_DQ_RE.search(text):
                risks.append(f"Eligibility / policy constraint: {text[:160]}")
        for advisory in ledger_result.advisory_scored_criteria:
            text = advisory.requirement_text or ""
            if _ELIGIBILITY_DQ_RE.search(text):
                risks.append(f"Scored eligibility criterion may be uncovered: {text[:160]}")

    seen: set[str] = set()
    unique: list[str] = []
    for risk in risks:
        key = risk.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(risk)
    return unique


def run_scan_dq_gate_pass(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    rfp_text: str,
    ledger_result: LedgerReconcileResult | None = None,
) -> ScanDqGateResult:
    """Gov-policy / DQ evaluate pass — legal attestation + go/no-go + RFP signals."""
    logs: list[str] = []
    human: list[str] = []
    changed = False

    try:
        from app.services.evidence_trust.legal_attestation_gate import (
            apply_legal_attestation_gates,
        )

        gated, att_report = apply_legal_attestation_gates(
            draft, rfp=rfp, rfp_context=rfp_text
        )
        if att_report.logs:
            logs.extend(f"legal-attestation: {line}" for line in att_report.logs[:12])
        flag_total = (
            att_report.everify_flags
            + att_report.conflict_flags
            + att_report.hours_flags
            + att_report.filler_flags
            + att_report.rno_flags
        )
        if flag_total:
            human.append(
                f"legal-attestation — {flag_total} gov-policy / attestation "
                "claim(s) gated (E-Verify, conflicts, invented hours/%, etc.); "
                "confirm before submission."
            )
        if gated.model_dump() != draft.model_dump():
            draft = gated
            changed = True
            logs.append("legal-attestation: manuscript updated by attestation gates.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan DQ legal attestation skipped: %s", exc)
        logs.append(f"legal-attestation skipped: {exc}")

    risks = collect_go_no_go_dq_risks(rfp)
    risks.extend(
        collect_rfp_text_dq_risks(
            rfp=rfp,
            draft=draft,
            rfp_text=rfp_text,
            ledger_result=ledger_result,
        )
    )
    # De-dupe again after merge
    seen: set[str] = set()
    unique_risks: list[str] = []
    for risk in risks:
        key = risk.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_risks.append(risk)

    review_gaps = collect_go_no_go_review_gaps(rfp)
    if review_gaps:
        human.append(
            f"go-no-go-review — {len(review_gaps)} capability / fit gap(s) from "
            "Go/No-Go (not automatic disqualifiers): "
            + "; ".join(review_gaps[:5])
        )
        logs.append(
            f"dq-gate — {len(review_gaps)} Go/No-Go review gap(s) kept separate "
            "from disqualification risks."
        )

    if unique_risks:
        logs.append(
            f"dq-gate — {len(unique_risks)} disqualification / gov-policy "
            "risk(s) surfaced for human review."
        )
        for risk in unique_risks[:8]:
            human.append(f"dq-risk — {risk}")

    return ScanDqGateResult(
        draft=draft,
        research=research,
        disqualification_risks=unique_risks,
        human_decision_gaps=human,
        logs=logs,
        changed=changed,
    )


def _ledger_needs_second_pass(
    ledger_result: LedgerReconcileResult,
    ledger_draft_logs: list[str],
) -> bool:
    """True when Pass 1 added coverage that should be rechecked once."""
    if ledger_result.applied_additions:
        return True
    if ledger_result.applied_merges or ledger_result.applied_cuts:
        return True
    if any("add-draft" in line for line in ledger_draft_logs):
        return True
    # Declined adds still warrant a DQ escalate, not another ADD attempt.
    return False


def merge_ledger_into_report(
    report: dict[str, Any],
    ledger_result: LedgerReconcileResult,
    ledger_draft_logs: list[str],
) -> None:
    report.setdefault("logs", []).extend(ledger_result.logs)
    report.setdefault("logs", []).extend(ledger_draft_logs)
    report["ledgerMergesApplied"] = (
        report.get("ledgerMergesApplied", 0) or 0
    ) + len(ledger_result.applied_merges)
    report["ledgerCutsApplied"] = (
        report.get("ledgerCutsApplied", 0) or 0
    ) + len(ledger_result.applied_cuts)
    report["ledgerAdditionsApplied"] = (
        report.get("ledgerAdditionsApplied", 0) or 0
    ) + len(ledger_result.applied_additions)
    titles = list(report.get("ledgerAdditionsSectionTitles") or [])
    titles.extend(a.section_title for a in ledger_result.applied_additions)
    report["ledgerAdditionsSectionTitles"] = titles
    merges = set(report.get("ledgerMergesSectionTitles") or [])
    merges.update(m.owner_section_title for m in ledger_result.applied_merges)
    report["ledgerMergesSectionTitles"] = sorted(merges)
    cuts = list(report.get("ledgerCutsSectionTitles") or [])
    cuts.extend(c.section_title for c in ledger_result.applied_cuts)
    report["ledgerCutsSectionTitles"] = cuts
    report["ledgerCheckSkippedReason"] = ledger_result.skipped_reason
    report["ledgerScoredCriteriaAdvisoryCount"] = len(
        ledger_result.advisory_scored_criteria
    )
    report["ledgerScoredCriteriaAdvisoryTitles"] = [
        a.requirement_text for a in ledger_result.advisory_scored_criteria
    ]
    report["ledgerSubmissionInstructionsCount"] = len(
        ledger_result.advisory_submission_instructions
    )
    report["ledgerSubmissionInstructionsTitles"] = [
        a.requirement_text for a in ledger_result.advisory_submission_instructions
    ]
    report["ledgerAdditionsDeclinedCount"] = ledger_result.declined_addition_count
    report["ledgerAdditionsDeclinedTitles"] = ledger_result.declined_addition_titles
    report["ledgerAdditionsDeclinedReason"] = ledger_result.declined_addition_reason


async def run_scan_coverage_orchestrator(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    rfp_text: str,
) -> ScanOrchestratorResult:
    """2-pass evaluate→act→recheck: ledger coverage + DQ/gov-policy gate."""
    from app.services.proposal_rfp_compliance import apply_scan_ledger_pass
    from app.services.proposal_repository import asave_proposal_draft

    logs: list[str] = []
    ledger_result: LedgerReconcileResult | None = None
    ledger_draft_logs: list[str] = []
    dq: ScanDqGateResult | None = None
    passes = 0

    for pass_idx in range(1, _MAX_ORCHESTRATOR_PASSES + 1):
        passes = pass_idx
        logs.append(f"orchestrator: pass {pass_idx}/{_MAX_ORCHESTRATOR_PASSES} — ledger coverage")
        draft, research, ledger_result, ledger_draft_logs = await apply_scan_ledger_pass(
            rfp_id=rfp_id,
            draft=draft,
            research=research,
            rfp=rfp,
            rfp_text=rfp_text,
        )
        logs.extend(ledger_result.logs)
        logs.extend(ledger_draft_logs)

        logs.append(
            f"orchestrator: pass {pass_idx}/{_MAX_ORCHESTRATOR_PASSES} — DQ / gov-policy gate"
        )
        dq = run_scan_dq_gate_pass(
            draft=draft,
            research=research,
            rfp=rfp,
            rfp_text=rfp_text,
            ledger_result=ledger_result,
        )
        draft = dq.draft
        research = dq.research
        logs.extend(dq.logs)
        if dq.changed:
            await asave_proposal_draft(draft)

        if pass_idx >= _MAX_ORCHESTRATOR_PASSES:
            break
        if not _ledger_needs_second_pass(ledger_result, ledger_draft_logs):
            logs.append(
                "orchestrator: coverage stable after pass 1 — skipping second ledger loop."
            )
            break
        logs.append(
            "orchestrator: pass 1 changed coverage — rechecking ledger once for "
            "remaining gaps / length."
        )

    if ledger_result and ledger_result.declined_addition_count:
        logs.append(
            "orchestrator: some required adds were declined by the blast-radius "
            "guard — left as humanDecisionGaps / checklist, not forced."
        )

    return ScanOrchestratorResult(
        draft=draft,
        research=research,
        ledger_result=ledger_result,
        ledger_draft_logs=ledger_draft_logs,
        dq=dq,
        loop_passes=passes,
        logs=logs,
    )


# Re-export for fulfill report merging
__all__ = [
    "ScanDqGateResult",
    "ScanOrchestratorResult",
    "collect_go_no_go_dq_risks",
    "collect_go_no_go_review_gaps",
    "collect_rfp_text_dq_risks",
    "run_scan_dq_gate_pass",
    "run_scan_coverage_orchestrator",
    "merge_ledger_into_report",
]
