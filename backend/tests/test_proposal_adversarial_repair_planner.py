from app.models.proposal import (
    RepairFailureReason,
    RepairPlan,
    RepairVerificationResult,
)


def test_repair_models_round_trip() -> None:
    plan = RepairPlan(
        findingCode="llm.fabrication.case_study_scope",
        findingCategory="fabrication",
        sectionId="section-20-portfolio",
        attemptNumber=2,
        previousOutcome="no_change",
        failureReason="evidence_missing",
        repairMode="targeted_retrieval_then_rewrite",
        requiresTargetedRetrieval=True,
        safePlanDrivenDraft=False,
        needsStrongModel=False,
        mustPreserveTags=True,
        allowedEvidenceIds=["E12", "E14"],
        successChecks=["finding_removed", "no_new_critical_findings"],
    )
    result = RepairVerificationResult(
        findingCode=plan.finding_code,
        sectionId=plan.section_id,
        resolved=False,
        improved=True,
        verifyCountDelta=-2,
        introducedCritical=False,
        outcome="improved_but_unresolved",
    )
    assert plan.failure_reason == "evidence_missing"
    assert result.outcome == "improved_but_unresolved"
