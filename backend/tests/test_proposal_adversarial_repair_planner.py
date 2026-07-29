from app.models.proposal import (
    AdversarialAuditFinding,
    RepairFailureReason,
    RepairPlan,
    RepairVerificationResult,
)
from app.services.proposal_adversarial_repair_planner import build_repair_plan


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


def test_fabrication_routes_to_targeted_retrieval() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.scope",
        message="Claimed scope is not supported.",
        sectionId="section-20",
        sectionTitle="Relevant Project Experience",
        source="llm",
    )
    plan = build_repair_plan(
        finding=finding,
        attempt_number=1,
        previous_outcome="",
        failure_reason=None,
    )
    assert plan.repair_mode == "targeted_retrieval_then_rewrite"
    assert plan.requires_targeted_retrieval is True
    assert plan.safe_plan_driven_draft is False


def test_empty_methodology_routes_to_plan_driven_rewrite() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="deterministic.coverage.empty_technical_approach",
        message="Required methodology section is empty.",
        sectionId="section-21-technical-approach",
        sectionTitle="Technical Approach & Methodology",
        source="deterministic",
    )
    plan = build_repair_plan(
        finding=finding,
        attempt_number=1,
        previous_outcome="",
        failure_reason=None,
    )
    assert plan.repair_mode == "plan_driven_rewrite"
    assert plan.safe_plan_driven_draft is True


def test_budget_finding_never_routes_to_freeform_rewrite() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="budget_money",
        code="deterministic.budget.mismatch",
        message="OF-2 total contradicts canonical budget.",
        sectionId="section-of2",
        sectionTitle="Offer Form OF-2",
        source="deterministic",
    )
    plan = build_repair_plan(
        finding=finding,
        attempt_number=1,
        previous_outcome="",
        failure_reason=None,
    )
    assert plan.repair_mode == "budget_canonical_repair"


def test_stubborn_evidence_missing_escalates_to_strong_model_at_attempt_two() -> None:
    """Strong-model escalation must be reachable before attempts are exhausted —
    with the default attempts cap of 3, waiting for attempt>=3 made this dead code."""
    finding = AdversarialAuditFinding(
        severity="critical",
        category="other",
        code="llm.consistency.unsupported_claim",
        message="Claim still lacks supporting evidence after rewrite.",
        sectionId="section-30",
        sectionTitle="Past Performance",
        source="llm",
    )
    plan = build_repair_plan(
        finding=finding,
        attempt_number=2,
        previous_outcome="improved_but_unresolved",
        failure_reason="evidence_missing",
    )
    assert plan.needs_strong_model is True
    assert plan.repair_mode == "strong_model_rewrite"


def test_fact_bound_portfolio_finding_never_routes_to_plan_driven_rewrite() -> None:
    finding = AdversarialAuditFinding(
        severity="critical",
        category="fabrication",
        code="llm.fabrication.portfolio",
        message="Portfolio scope invented.",
        sectionId="section-20",
        sectionTitle="Relevant Project Experience",
        source="llm",
    )
    plan = build_repair_plan(
        finding=finding,
        attempt_number=2,
        previous_outcome="no_change",
        failure_reason="evidence_missing",
    )
    assert plan.safe_plan_driven_draft is False
