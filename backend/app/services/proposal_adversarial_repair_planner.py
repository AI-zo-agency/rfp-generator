"""Finding-centered repair planning for adversarial repair loop."""

from __future__ import annotations

from app.models.proposal import AdversarialAuditFinding, RepairPlan

_METHODOLOGY_TITLE_TOKENS = (
    "technical approach",
    "methodology",
    "training",
    "timeline",
    "knowledge transfer",
    "transmittal",
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
    code = (finding.code or "").casefold()
    category = (finding.category or "other").casefold()
    title = (finding.section_title or "").casefold()
    message = (finding.message or "").casefold()

    if "budget" in category or "money" in category or "commission" in code:
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

    if category == "fabrication":
        return RepairPlan(
            findingCode=finding.code,
            findingCategory=finding.category,
            sectionId=finding.section_id,
            attemptNumber=attempt_number,
            previousOutcome=previous_outcome,
            failureReason=failure_reason,
            repairMode="targeted_retrieval_then_rewrite",
            requiresTargetedRetrieval=True,
            safePlanDrivenDraft=False,
            successChecks=["finding_removed", "no_new_critical_findings"],
        )

    if any(token in title for token in _METHODOLOGY_TITLE_TOKENS):
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

    if any(marker in code or marker in message for marker in _EMPTY_SECTION_CODE_MARKERS):
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

    return RepairPlan(
        findingCode=finding.code,
        findingCategory=finding.category,
        sectionId=finding.section_id,
        attemptNumber=attempt_number,
        previousOutcome=previous_outcome,
        failureReason=failure_reason,
        repairMode="targeted_retrieval_then_rewrite",
        requiresTargetedRetrieval=True,
        successChecks=["finding_removed"],
    )
