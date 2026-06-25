from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProposalSectionMode = Literal["pull", "select", "write"]
ProposalSectionSource = Literal["template", "rfp", "generated"]
ProposalSectionStatus = Literal["empty", "outline", "generated", "reviewed"]
ZoSectionMode = Literal["pull", "select", "write"]


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
    updated_at: str = Field(alias="updatedAt")
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
