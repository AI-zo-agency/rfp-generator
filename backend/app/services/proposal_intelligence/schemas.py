"""Structured JSON schemas for the Proposal Intelligence Layer (Phase 2)."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

CONFIDENCE_WARN_THRESHOLD = 0.70
PLAN_VERSION = "1.0"

ExpectedSource = Literal[
    "won_proposals",
    "case_studies",
    "testimonials",
    "references",
    "methodology",
    "pricing",
    "bios",
    "company_facts",
    "portfolio",
    "images",
    "diagrams",
    "playbooks",
    "standards",
]


class DecisionLogEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent: str
    decision: str
    reason: str
    confidence: float = 0.0


class ProposalMemory(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    facts: dict[str, str] = Field(default_factory=dict)
    updated_by: list[str] = Field(default_factory=list, alias="updatedBy")
    confidence: float = 1.0


class PlanValidation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    readiness_status: Literal["ready", "blocked", "partial"] = Field(
        default="partial", alias="readinessStatus"
    )
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    consistency_checks: list[str] = Field(default_factory=list, alias="consistencyChecks")
    low_confidence_artifacts: list[str] = Field(
        default_factory=list, alias="lowConfidenceArtifacts"
    )


class LayerStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    opportunity: Literal["pending", "complete", "failed"] = "pending"
    delivery: Literal["pending", "complete", "failed"] = "pending"
    writing: Literal["pending", "complete", "failed"] = "pending"


class PlanMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(default="", alias="rfpId")
    generated_at: str = Field(default="", alias="generatedAt")
    provider: str | None = None
    plan_version: str = Field(default=PLAN_VERSION, alias="planVersion")
    generation_mode: str = Field(default="intelligence", alias="generationMode")
    won_patterns_used: list[str] = Field(default_factory=list, alias="wonPatternsUsed")
    plan_confidence: float = Field(default=0.0, alias="planConfidence")
    validation_status: str = Field(default="partial", alias="validationStatus")
    layer_status: LayerStatus = Field(default_factory=LayerStatus, alias="layerStatus")


class BudgetIntel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ceiling: str | None = None
    pricing_model_hint: str | None = Field(default=None, alias="pricingModelHint")
    contract_type: str | None = Field(default=None, alias="contractType")
    notes: str = ""


class TimelineIntel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_start: str | None = Field(default=None, alias="projectStart")
    completion: str | None = None
    go_live: str | None = Field(default=None, alias="goLive")
    milestones: list[str] = Field(default_factory=list)
    notes: str = ""


class OpportunityUnderstanding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client: str = ""
    industry: str = ""
    org_type: str = Field(default="", alias="orgType")
    project_type: str = Field(default="", alias="projectType")
    services: list[str] = Field(default_factory=list)
    business_goals: list[str] = Field(default_factory=list, alias="businessGoals")
    pain_points: list[str] = Field(default_factory=list, alias="painPoints")
    desired_outcomes: list[str] = Field(default_factory=list, alias="desiredOutcomes")
    complexity: str = ""
    budget_intel: BudgetIntel = Field(default_factory=BudgetIntel, alias="budgetIntel")
    timeline_intel: TimelineIntel = Field(default_factory=TimelineIntel, alias="timelineIntel")
    confidence: float = 0.0


class ComplianceItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    requirement: str
    mandatory: bool = True
    source_ref: str = Field(default="", alias="sourceRef")
    target_section: str = Field(default="", alias="targetSection")
    evidence_needed: str = Field(default="", alias="evidenceNeeded")
    status: str = "open"
    owner: str = ""


class ComplianceMatrix(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[ComplianceItem] = Field(default_factory=list)
    confidence: float = 0.0


class ScopeAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mandatory: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    future_phases: list[str] = Field(default_factory=list, alias="futurePhases")
    out_of_scope: list[str] = Field(default_factory=list, alias="outOfScope")
    dependencies: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class EvaluationCriterion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    weight: float | None = None
    priority_rank: int | None = Field(default=None, alias="priorityRank")


class EvaluationAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    criteria: list[EvaluationCriterion] = Field(default_factory=list)
    emphasis: list[str] = Field(default_factory=list)
    writing_style: str = Field(default="", alias="writingStyle")
    confidence: float = 0.0


class SuccessCriterion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    criterion: str
    why: str = ""
    recurring_theme: bool = Field(default=False, alias="recurringTheme")


class SuccessCriteriaResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[SuccessCriterion] = Field(default_factory=list)
    confidence: float = 0.0


class OpportunityStrategy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    winning_theme: str = Field(default="", alias="winningTheme")
    core_message: str = Field(default="", alias="coreMessage")
    differentiators: list[str] = Field(default_factory=list)
    trust_builders: list[str] = Field(default_factory=list, alias="trustBuilders")
    risk_mitigation: list[str] = Field(default_factory=list, alias="riskMitigation")
    proof_strategy: str = Field(default="", alias="proofStrategy")
    tone: str = ""
    key_messages: list[str] = Field(default_factory=list, alias="keyMessages")
    primary_evaluator_concerns: list[str] = Field(
        default_factory=list, alias="primaryEvaluatorConcerns"
    )
    competitive_position: str = Field(default="", alias="competitivePosition")
    why_us: str = Field(default="", alias="whyUs")
    executive_narrative: str = Field(default="", alias="executiveNarrative")
    confidence: float = 0.0


class OpportunityIntelligence(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    understanding: OpportunityUnderstanding = Field(default_factory=OpportunityUnderstanding)
    compliance: ComplianceMatrix = Field(default_factory=ComplianceMatrix)
    scope: ScopeAnalysis = Field(default_factory=ScopeAnalysis)
    evaluation: EvaluationAnalysis = Field(default_factory=EvaluationAnalysis)
    success_criteria: SuccessCriteriaResult = Field(
        default_factory=SuccessCriteriaResult, alias="successCriteria"
    )
    strategy: OpportunityStrategy = Field(default_factory=OpportunityStrategy)


class DeliveryModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = ""
    governance: str = ""
    cadence: str = ""
    client_engagement: str = Field(default="", alias="clientEngagement")
    review_model: str = Field(default="", alias="reviewModel")
    decision_making: str = Field(default="", alias="decisionMaking")
    confidence: float = 0.0


class DeliveryPattern(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patterns_observed: list[str] = Field(default_factory=list, alias="patternsObserved")
    source_won_proposals: list[str] = Field(default_factory=list, alias="sourceWonProposals")
    staffing_shape: str = Field(default="", alias="staffingShape")
    phase_shape: str = Field(default="", alias="phaseShape")
    confidence: float = 0.0


class MethodologyPhase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    activities: list[str] = Field(default_factory=list)
    governance: str = ""


class MethodologyPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phases: list[MethodologyPhase] = Field(default_factory=list)
    confidence: float = 0.0


class WorkPackage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    work_package: str = Field(alias="workPackage")
    phase: str = ""
    deliverables: list[str] = Field(default_factory=list)


class WorkBreakdown(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    packages: list[WorkPackage] = Field(default_factory=list)
    confidence: float = 0.0


class TimelineMilestone(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    offset: str = ""
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")


class TimelinePlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    milestones: list[TimelineMilestone] = Field(default_factory=list)
    go_live: str = Field(default="", alias="goLive")
    review_cycles: str = Field(default="", alias="reviewCycles")
    confidence: float = 0.0


class RoleEffort(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str
    hours: float | None = None
    notes: str = ""


class BudgetPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pricing_strategy: str = Field(default="", alias="pricingStrategy")
    pricing_model: str = Field(default="", alias="pricingModel")
    pricing_tiers: str = Field(default="", alias="pricingTier")
    contract_type: str = Field(default="", alias="contractType")
    ceiling: str = ""
    constraints: list[str] = Field(default_factory=list)
    cost_weight: float | None = Field(default=None, alias="costWeight")
    pricing_validation: str = Field(default="", alias="pricingValidation")
    role_effort: list[RoleEffort] = Field(default_factory=list, alias="roleEffort")
    confidence: float = 0.0


class ResourceAllocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str
    allocation_pct: float | None = Field(default=None, alias="allocationPct")
    phase: str = ""


class ResourcePlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    allocations: list[ResourceAllocation] = Field(default_factory=list)
    confidence: float = 0.0


class RiskItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    risk: str
    likelihood: str = ""
    impact: str = ""
    mitigation: str = ""


class RiskPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    risks: list[RiskItem] = Field(default_factory=list)
    confidence: float = 0.0


class QaPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    approach: str = ""
    gates: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class CommunicationPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cadence: str = ""
    channels: list[str] = Field(default_factory=list)
    reporting_plan: str = Field(default="", alias="reportingPlan")
    confidence: float = 0.0


class TrainingPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    training_plan: str = Field(default="", alias="trainingPlan")
    transition_plan: str = Field(default="", alias="transitionPlan")
    confidence: float = 0.0


class DeliveryIntelligence(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    delivery_model: DeliveryModel = Field(default_factory=DeliveryModel, alias="deliveryModel")
    delivery_pattern: DeliveryPattern = Field(
        default_factory=DeliveryPattern, alias="deliveryPattern"
    )
    methodology: MethodologyPlan = Field(default_factory=MethodologyPlan)
    work_breakdown: WorkBreakdown = Field(default_factory=WorkBreakdown, alias="workBreakdown")
    timeline: TimelinePlan = Field(default_factory=TimelinePlan)
    budget: BudgetPlan = Field(default_factory=BudgetPlan)
    resources: ResourcePlan = Field(default_factory=ResourcePlan)
    risk: RiskPlan = Field(default_factory=RiskPlan)
    qa: QaPlan = Field(default_factory=QaPlan)
    communication: CommunicationPlan = Field(default_factory=CommunicationPlan)
    training: TrainingPlan = Field(default_factory=TrainingPlan)


class OutlineSection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    order: int = 0
    required: bool = True
    conditional_reason: str = Field(default="", alias="conditionalReason")
    parent_id: str | None = Field(default=None, alias="parentId")
    children: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class ProposalOutline(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sections: list[OutlineSection] = Field(default_factory=list)
    confidence: float = 0.0


class WinningPattern(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_won_proposals: list[str] = Field(default_factory=list, alias="sourceWonProposals")
    opening_pattern: str = Field(default="", alias="openingPattern")
    structure_flow: list[str] = Field(default_factory=list, alias="structureFlow")
    persuasion_techniques: list[str] = Field(
        default_factory=list, alias="persuasionTechniques"
    )
    common_differentiators: list[str] = Field(
        default_factory=list, alias="commonDifferentiators"
    )
    common_objections: list[str] = Field(default_factory=list, alias="commonObjections")
    recommended_word_count: int | None = Field(default=None, alias="recommendedWordCount")
    recommended_visuals: list[str] = Field(default_factory=list, alias="recommendedVisuals")
    avoid: list[str] = Field(default_factory=list)
    common_proof_themes: list[str] = Field(default_factory=list, alias="commonProofThemes")
    confidence: float = 0.0


class SectionPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    title: str = ""
    purpose: str = ""
    key_messages: list[str] = Field(default_factory=list, alias="keyMessages")
    evaluation_criteria: list[str] = Field(default_factory=list, alias="evaluationCriteria")
    evidence_needed: list[str] = Field(default_factory=list, alias="evidenceNeeded")
    retrieval_goal: str = Field(default="", alias="retrievalGoal")
    writer_instructions: str = Field(default="", alias="writerInstructions")
    success_definition: str = Field(default="", alias="successDefinition")
    word_budget: int = Field(default=800, alias="wordBudget")
    tone: str = ""
    voice_register: str = Field(default="narrative", alias="register")
    audience: str = ""
    winning_pattern: WinningPattern = Field(
        default_factory=WinningPattern, alias="winningPattern"
    )


class SectionPlans(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plans: list[SectionPlan] = Field(default_factory=list)
    confidence: float = 0.0


class RetrievalEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    required_assets: list[str] = Field(default_factory=list, alias="requiredAssets")
    queries: list[str] = Field(default_factory=list)
    priority: str = "required"
    constraints: list[str] = Field(default_factory=list)
    expected_sources: list[str] = Field(default_factory=list, alias="expectedSources")
    why_needed: str = Field(default="", alias="whyNeeded")


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entries: list[RetrievalEntry] = Field(default_factory=list)
    confidence: float = 0.0


class WritingIntelligence(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    proposal_outline: ProposalOutline = Field(
        default_factory=ProposalOutline, alias="proposalOutline"
    )
    section_plans: SectionPlans = Field(default_factory=SectionPlans, alias="sectionPlans")
    retrieval_plan: RetrievalPlan = Field(default_factory=RetrievalPlan, alias="retrievalPlan")
    reviewer_personas: Any | None = Field(default=None, alias="reviewerPersonas")


class ProposalExecutionPlan(BaseModel):
    """Canonical Phase 2 contract — single source of truth for Phase 3 writers."""

    model_config = ConfigDict(populate_by_name=True)

    evidence_corpus_rule: ClassVar[str] = "phase3_only"

    metadata: PlanMetadata = Field(default_factory=PlanMetadata)
    opportunity: OpportunityIntelligence = Field(default_factory=OpportunityIntelligence)
    delivery: DeliveryIntelligence = Field(default_factory=DeliveryIntelligence)
    writing: WritingIntelligence = Field(default_factory=WritingIntelligence)
    proposal_memory: ProposalMemory = Field(default_factory=ProposalMemory, alias="proposalMemory")
    decision_log: list[DecisionLogEntry] = Field(default_factory=list, alias="decisionLog")
    validation: PlanValidation = Field(default_factory=PlanValidation)

    def __init__(self, **data: Any) -> None:
        rfp_id = data.pop("rfpId", None) or data.pop("rfp_id", None)
        super().__init__(**data)
        if rfp_id and not self.metadata.rfp_id:
            self.metadata.rfp_id = str(rfp_id)
