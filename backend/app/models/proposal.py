from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProposalSectionMode = Literal["pull", "select", "write"]
ProposalSectionSource = Literal["template", "rfp", "generated"]
ProposalSectionStatus = Literal["empty", "outline", "generated", "reviewed"]
ZoSectionMode = Literal["pull", "select", "write"]
BudgetLineItemType = Literal["agency_fee", "client_passthrough", "direct_expense"]


class RfpSectionMap(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    page_limit: int | None = Field(default=None, alias="pageLimit")
    requirements: list[str] = Field(default_factory=list)
    retrieval_focus: list[str] = Field(default_factory=list, alias="retrievalFocus")
    zo_mode: ZoSectionMode = Field(default="write", alias="zoMode")
    evaluation_weight: int | None = Field(default=None, alias="evaluationWeight")
    coverage_percent: int | None = Field(default=None, alias="coveragePercent")
    uncovered_requirements: list[str] = Field(
        default_factory=list, alias="uncoveredRequirements"
    )


class EvidenceItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    source: str
    excerpt: str
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    chunk_key: str = Field(default="", alias="chunkKey")


class ResearchQuestion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    topic: str
    question: str
    answer: str | None = None
    sources: list[str] = Field(default_factory=list)


class ProposalBrandVoice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tone: str = ""
    formality: str = "semi-formal"
    voice_guidelines: list[str] = Field(default_factory=list, alias="voiceGuidelines")
    key_terms: list[str] = Field(default_factory=list, alias="keyTerms")
    client_expectations: str = Field(default="", alias="clientExpectations")
    zo_core_voice: str = Field(default="", alias="zoCoreVoice")
    rfp_adaptation_notes: str = Field(default="", alias="rfpAdaptationNotes")
    kb_zo_voice: str = Field(default="", alias="kbZoVoice")



class BudgetLineItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    category: str
    description: str
    named_person: str | None = Field(default=None, alias="namedPerson")
    role_title: str | None = Field(default=None, alias="roleTitle")
    unit: str = "flat"
    quantity: float | None = None
    rate: float | None = None
    rate_source: str = Field(default="", alias="rateSource")
    extended: float | None = None
    notes: str | None = None
    line_item_type: BudgetLineItemType | None = Field(default=None, alias="lineItemType")


class VerifiedRate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    person_name: str = Field(alias="personName")
    role: str = ""
    hourly_rate: float | None = Field(default=None, alias="hourlyRate")
    source: str = ""


class LossLesson(BaseModel):
    """Pattern from lost proposals / scoring debriefs to avoid in this bid."""

    model_config = ConfigDict(populate_by_name=True)

    pattern: str
    avoid: str
    reason: str = ""
    source: str = ""
    relevance: str = "medium"


class ProofPoint(BaseModel):
    """Maps an RFP requirement to a verified zö case study / proof."""

    model_config = ConfigDict(populate_by_name=True)

    requirement: str
    case_study: str = Field(alias="caseStudy")
    kb_source: str = Field(default="", alias="kbSource")
    narrative_hook: str = Field(default="", alias="narrativeHook")
    relevance: str = "high"
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    evaluation_weight: int | None = Field(default=None, alias="evaluationWeight")


class FeeJustificationMemo(BaseModel):
    """Internal fee defense memo (not for submission)."""

    model_config = ConfigDict(populate_by_name=True)

    markdown: str
    pricing_posture: str = Field(default="", alias="pricingPosture")
    target_vs_cap: str = Field(default="", alias="targetVsCap")
    role_hours_summary: list[str] = Field(default_factory=list, alias="roleHoursSummary")
    internal_notes: list[str] = Field(default_factory=list, alias="internalNotes")
    generated_at: str = Field(alias="generatedAt")
    provider: str | None = None


class PreSubmitIssue(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    severity: Literal["critical", "warning", "info"]
    category: str
    message: str
    section_id: str | None = Field(default=None, alias="sectionId")
    section_title: str | None = Field(default=None, alias="sectionTitle")
    excerpt: str | None = None


class ComplianceCheckItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item: str
    status: Literal["pass", "fail", "manual"]
    notes: str = ""


class ManualFillFlag(BaseModel):
    """Submission gap flagged for human completion after KB + final editor pass."""

    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    section_title: str = Field(alias="sectionTitle")
    kind: Literal[
        "verify",
        "placeholder",
        "manual_fill",
        "compliance",
        "budget",
        "consistency",
        "other",
    ] = "other"
    tag: str
    highlight_text: str | None = Field(default=None, alias="highlightText")
    owner: str | None = None
    finalized: bool = False
    kb_searched: bool = Field(default=False, alias="kbSearched")


class PreSubmitReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(alias="rfpId")
    issues: list[PreSubmitIssue] = Field(default_factory=list)
    compliance_checklist: list[ComplianceCheckItem] = Field(
        default_factory=list, alias="complianceChecklist"
    )
    manual_fill_flags: list[ManualFillFlag] = Field(
        default_factory=list, alias="manualFillFlags"
    )
    summary: str = ""
    issues_markdown: str = Field(default="", alias="issuesMarkdown")
    ready_to_submit: bool = Field(default=False, alias="readyToSubmit")
    scanned_at: str = Field(alias="scannedAt")
    provider: str | None = None


class SectionAutoFixLog(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    section_title: str = Field(alias="sectionTitle")
    iteration: int
    methods: list[str] = Field(default_factory=list)
    issues_targeted: int = Field(default=0, alias="issuesTargeted")


class PreSubmitAutoFixReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    iterations_run: int = Field(alias="iterationsRun")
    issues_before: int = Field(alias="issuesBefore")
    issues_after: int = Field(alias="issuesAfter")
    sections_patched: int = Field(alias="sectionsPatched")
    sections_targeted: int = Field(default=0, alias="sectionsTargeted")
    stopped_reason: str = Field(alias="stoppedReason")
    section_logs: list[SectionAutoFixLog] = Field(default_factory=list, alias="sectionLogs")


class PricingTier(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    total: float | None = None
    line_item_ids: list[str] = Field(default_factory=list, alias="lineItemIds")
    rationale: str = ""


class ProposalBudget(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(alias="rfpId")
    rfp_budget_cap: float | None = Field(default=None, alias="rfpBudgetCap")
    rfp_budget_notes: str = Field(default="", alias="rfpBudgetNotes")
    fee_structure: str = Field(default="", alias="feeStructure")
    pricing_tier: str | None = Field(default=None, alias="pricingTier")
    budget_format: str | None = Field(default=None, alias="budgetFormat")
    line_items: list[BudgetLineItem] = Field(default_factory=list, alias="lineItems")
    tiers: list[PricingTier] = Field(default_factory=list)
    recommended_tier_id: str | None = Field(default=None, alias="recommendedTierId")
    agency_revenue_estimate: float | None = Field(
        default=None, alias="agencyRevenueEstimate"
    )
    line_item_sum: float | None = Field(default=None, alias="lineItemSum")
    agency_fee_subtotal: float | None = Field(default=None, alias="agencyFeeSubtotal")
    client_media_passthrough: float | None = Field(
        default=None, alias="clientMediaPassthrough"
    )
    total_client_invoicing: float | None = Field(
        default=None, alias="totalClientInvoicing"
    )
    commission_rate: float | None = Field(default=None, alias="commissionRate")
    lump_sum_total: float | None = Field(default=None, alias="lumpSumTotal")
    direct_expenses_total: float | None = Field(default=None, alias="directExpensesTotal")
    commission_model: str | None = Field(default=None, alias="commissionModel")
    pricing_flags: list[str] = Field(default_factory=list, alias="pricingFlags")
    qualifying_language: str = Field(default="", alias="qualifyingLanguage")
    scope_adjustments: list[str] = Field(default_factory=list, alias="scopeAdjustments")
    scope_summary: str = Field(default="", alias="scopeSummary")
    design_brief: str = Field(default="", alias="designBrief")
    option_term_notes: str = Field(default="", alias="optionTermNotes")
    media_spend_notes: str = Field(default="", alias="mediaSpendNotes")
    verified_rates: list[VerifiedRate] = Field(default_factory=list, alias="verifiedRates")
    kb_sources: list[str] = Field(default_factory=list, alias="kbSources")
    kb_buckets_used: list[str] = Field(default_factory=list, alias="kbBucketsUsed")
    confidence: int = 0
    fee_justification_memo: FeeJustificationMemo | None = Field(
        default=None, alias="feeJustificationMemo"
    )
    updated_at: str = Field(alias="updatedAt")
    provider: str | None = None


class ProposalPipelineCheckpoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    last_completed_phase: str | None = Field(default=None, alias="lastCompletedPhase")
    in_progress_phase: str | None = Field(default=None, alias="inProgressPhase")
    last_failed_phase: str | None = Field(default=None, alias="lastFailedPhase")
    last_error: str | None = Field(default=None, alias="lastError")
    resume_from_phase: str | None = Field(default=None, alias="resumeFromPhase")
    updated_at: str = Field(alias="updatedAt")


class Section1EditorialRecommendation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    section_title: str = Field(alias="sectionTitle")
    issue_type: str = Field(alias="issueType")
    issue: str
    recommendation: str
    confidence: float = 0.0
    suggested_replacement: str | None = Field(default=None, alias="suggestedReplacement")
    status: Literal["pending", "approved", "rejected"] = "pending"


class Section1EditorialReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reviewed_at: str = Field(alias="reviewedAt")
    recommendations: list[Section1EditorialRecommendation] = Field(default_factory=list)
    provider: str | None = None


class ProposalResearchCache(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(alias="rfpId")
    rfp_sections: list[RfpSectionMap] = Field(default_factory=list, alias="rfpSections")
    questions: list[ResearchQuestion] = Field(default_factory=list)
    brand_voice: ProposalBrandVoice | None = Field(default=None, alias="brandVoice")
    evidence_corpus: list[EvidenceItem] = Field(default_factory=list, alias="evidenceCorpus")
    section_queries: dict[str, list[str]] = Field(default_factory=dict, alias="sectionQueries")
    retrieval_rounds: int = Field(default=0, alias="retrievalRounds")
    coverage_threshold: int = Field(default=85, alias="coverageThreshold")
    budget: ProposalBudget | None = None
    loss_lessons: list[LossLesson] = Field(default_factory=list, alias="lossLessons")
    writing_avoidances: list[str] = Field(default_factory=list, alias="writingAvoidances")
    proof_points: list[ProofPoint] = Field(default_factory=list, alias="proofPoints")
    presubmit_review: PreSubmitReview | None = Field(default=None, alias="presubmitReview")
    section1_editorial_review: Section1EditorialReview | None = Field(
        default=None, alias="section1EditorialReview"
    )
    pipeline_checkpoint: ProposalPipelineCheckpoint | None = Field(
        default=None, alias="pipelineCheckpoint"
    )
    updated_at: str = Field(alias="updatedAt")
    provider: str | None = None


class ProposalSection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    page_limit: int | None = Field(default=None, alias="pageLimit")
    word_target: int = Field(default=900, alias="wordTarget")
    required: bool = True
    custom: bool = False
    source: ProposalSectionSource = "template"
    mode: ProposalSectionMode = "pull"
    content: str = ""
    designer_note: str | None = Field(default=None, alias="designerNote")
    status: ProposalSectionStatus = "outline"
    kb_refs: list[str] = Field(default_factory=list, alias="kbRefs")


class ProposalDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rfp_id: str = Field(alias="rfpId")
    sections: list[ProposalSection]
    updated_at: str = Field(alias="updatedAt")
    generated_at: str | None = Field(default=None, alias="generatedAt")
    provider: str | None = None


class ProposalGenerateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    draft: ProposalDraft
    research: ProposalResearchCache | None = None
    brand_voice: ProposalBrandVoice | None = Field(default=None, alias="brandVoice")


class ProposalPhase2Response(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    research: ProposalResearchCache


class ProposalSectionImproveResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    section: ProposalSection
    draft: ProposalDraft
    research: ProposalResearchCache
    assistant_message: str = Field(alias="assistantMessage")


class SectionImproveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(min_length=1, max_length=4000)
    selection_start: int | None = Field(default=None, alias="selectionStart", ge=0)
    selection_end: int | None = Field(default=None, alias="selectionEnd", ge=0)
    selection_text: str | None = Field(default=None, alias="selectionText", max_length=8000)


class ProposalPhase3Response(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    draft: ProposalDraft
    research: ProposalResearchCache


class ProposalPricingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    budget: ProposalBudget
    research: ProposalResearchCache
    draft: ProposalDraft | None = None


class ProposalPhase4Response(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    review: PreSubmitReview
    research: ProposalResearchCache
    draft: ProposalDraft | None = None


class ProposalPhase4AutoFixResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    review: PreSubmitReview
    research: ProposalResearchCache
    draft: ProposalDraft
    auto_fix: PreSubmitAutoFixReport = Field(alias="autoFix")


class PreSubmitAutoFixRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    use_llm: bool = Field(default=True, alias="useLlm")
