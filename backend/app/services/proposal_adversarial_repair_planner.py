"""Finding-centered repair planning for adversarial repair loop."""

from __future__ import annotations

from app.models.proposal import AdversarialAuditFinding, RepairPlan
from app.services.proposal_evidence_gate import (
    EvidenceDecision,
    decide_evidence_action,
    gate_to_repair_mode,
)

_EMPTY_SECTION_CODE_MARKERS = ("coverage.empty", "empty_section")

# Failure reasons that indicate a weaker rewrite pass is stuck rather than making
# progress — escalate to the strong-model prompt path instead of repeating the
# same weak-model strategy indefinitely.
_STRONG_ESCALATION_FAILURE_REASONS = frozenset(
    {"still_unverified", "evidence_missing", "no_change", "retrieval_too_weak"}
)


def build_repair_plan(
    *,
    finding: AdversarialAuditFinding,
    attempt_number: int,
    previous_outcome: str,
    failure_reason: str | None,
) -> RepairPlan:
    # Escalate stubborn failures to the strong-model prompt path before attempt 3
    # (attempts_cap defaults to 3, so waiting for attempt>=3 made this unreachable
    # in practice) — a second attempt that is still unverified, missing evidence,
    # unchanged, or under-retrieved is unlikely to converge on a third weak-model
    # repeat of the same strategy.
    if attempt_number >= 2 and failure_reason in _STRONG_ESCALATION_FAILURE_REASONS:
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="strong_model_rewrite",
            needsStrongModel=True,
            requiresTargetedRetrieval=failure_reason
            in {"evidence_missing", "retrieval_too_weak"},
            successChecks=["finding_removed"],
        )

    if attempt_number >= 3:
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="strong_model_rewrite",
            needsStrongModel=True,
            successChecks=["finding_removed"],
        )

    decision = decide_evidence_action(
        section_id=finding.section_id,
        section_title=finding.section_title,
        finding=finding,
    )
    mode = gate_to_repair_mode(decision)

    if decision.action == EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET:
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="budget_canonical_repair",
            successChecks=["finding_removed", "budget_still_grounded"],
        )

    if decision.action == EvidenceDecision.MANUAL_FILL:
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="protected_skip",
            successChecks=["finding_removed"],
        )

    if decision.action == EvidenceDecision.DETERMINISTIC_CLEANUP:
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="deterministic_cleanup",
            successChecks=["finding_removed"],
        )

    if decision.action == EvidenceDecision.WRITE_FROM_PLAN or any(
        marker in (finding.code or "").casefold()
        or marker in (finding.message or "").casefold()
        for marker in _EMPTY_SECTION_CODE_MARKERS
    ):
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="plan_driven_rewrite",
            safePlanDrivenDraft=True,
            successChecks=["section_non_empty", "no_unsupported_fact_claims"],
        )

    return RepairPlan(
        findingCode=finding.code,
        findingCategory=finding.category,
        sectionId=finding.section_id,
        attemptNumber=attempt_number,
        previousOutcome=previous_outcome,
        failureReason=failure_reason,
        repairMode=mode if mode != "protected_skip" else "targeted_retrieval_then_rewrite",
        requiresTargetedRetrieval=decision.requires_retrieval
        or decision.action == EvidenceDecision.RETRIEVE_THEN_WRITE,
        safePlanDrivenDraft=decision.safe_plan_driven,
        successChecks=["finding_removed", "no_new_critical_findings"]
        if decision.action == EvidenceDecision.RETRIEVE_THEN_WRITE
        else ["finding_removed"],
    )
