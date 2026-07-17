"""Structured JSON schemas for the Company Qualification Layer (Phase 1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CapabilityTierItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    capability: str
    rationale: str = ""


class PrioritizedCapabilities(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    primary: list[CapabilityTierItem] = Field(default_factory=list)
    secondary: list[CapabilityTierItem] = Field(default_factory=list)
    omit: list[CapabilityTierItem] = Field(default_factory=list)


class CompanyLocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    office: str | None = None
    mailing: str | None = None
    remittance: str | None = None


class CompanyContact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phone: str | None = None
    email: str | None = None
    website: str | None = None


class StateRegistration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    state: str
    id: str = ""


class BusinessRegistration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ein: str | None = None
    state_ids: list[StateRegistration] = Field(default_factory=list, alias="stateIds")


class Department(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    head: str | None = None
    summary: str | None = None


class Certification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    agency: str | None = None
    number: str | None = None
    expires: str | None = None


class InsuranceCoverage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    amount: str | None = None


class CompanyTruth(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    legal_name: str | None = Field(default=None, alias="legalName")
    dba: str | None = None
    founded: str | None = None
    years_in_operation: int | None = Field(default=None, alias="yearsInOperation")
    ownership: str | None = None
    locations: CompanyLocation | None = None
    contact: CompanyContact | None = None
    business_registration: BusinessRegistration | None = Field(
        default=None, alias="businessRegistration"
    )
    employee_count: str | None = Field(default=None, alias="employeeCount")
    departments: list[Department] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    insurance: list[InsuranceCoverage] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class ProposalContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    industry: str = ""
    services_requested: list[str] = Field(default_factory=list, alias="servicesRequested")
    buyer_type: str = Field(default="", alias="buyerType")
    evaluation_priorities: list[str] = Field(default_factory=list, alias="evaluationPriorities")
    project_complexity: Literal["low", "medium", "high"] = Field(
        default="medium", alias="projectComplexity"
    )
    proposal_type: str = Field(default="", alias="proposalType")
    summary: str = ""


class SubsectionBudget(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    title: str
    format: Literal["narrative", "table", "list", "facts"] = "narrative"
    word_min: int | None = Field(default=None, alias="wordMin")
    word_max: int | None = Field(default=None, alias="wordMax")
    notes: str | None = None


class Section1ContentBudget(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    budgets: list[SubsectionBudget] = Field(default_factory=list)


class SubsectionPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    included_capabilities: list[str] = Field(default_factory=list, alias="includedCapabilities")
    omitted_capabilities: list[str] = Field(default_factory=list, alias="omittedCapabilities")
    target_words: dict[str, int | None] = Field(default_factory=dict, alias="targetWords")


class GeneratedSubsection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    content: str = ""
    word_count: int = Field(default=0, alias="wordCount")


class Section1CompositionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_plan: dict[str, SubsectionPlan] = Field(default_factory=dict, alias="sectionPlan")
    generated_sections: list[GeneratedSubsection] = Field(
        default_factory=list, alias="generatedSections"
    )


class EditorialRecommendation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    section_title: str = Field(alias="sectionTitle")
    issue_type: str = Field(alias="issueType")
    issue: str
    recommendation: str
    confidence: float = 0.0
    suggested_replacement: str | None = Field(default=None, alias="suggestedReplacement")
    status: Literal["pending", "approved", "rejected"] = "pending"


class EditorialReviewResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    recommendations: list[EditorialRecommendation] = Field(default_factory=list)


class Section1EditorialReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reviewed_at: str = Field(alias="reviewedAt")
    recommendations: list[EditorialRecommendation] = Field(default_factory=list)
    provider: str | None = None


class Section1PlanResult(BaseModel):
    """Section 1 Agent output — budgets + inclusion plan, no prose."""

    model_config = ConfigDict(populate_by_name=True)

    content_budget: Section1ContentBudget = Field(alias="contentBudget")
    section_plan: dict[str, SubsectionPlan] = Field(default_factory=dict, alias="sectionPlan")


class TeamMemberSelection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    role: str = ""
    rationale: str = ""
    fit_score: float = Field(default=0.0, alias="fitScore")
    matched_skills: list[str] = Field(default_factory=list, alias="matchedSkills")


class RequiredTeamRole(BaseModel):
    """RFP staffing need extracted before any person is chosen."""

    model_config = ConfigDict(populate_by_name=True)

    role: str
    must_have_skills: list[str] = Field(default_factory=list, alias="mustHaveSkills")
    nice_to_have_skills: list[str] = Field(default_factory=list, alias="niceToHaveSkills")
    why_needed: str = Field(default="", alias="whyNeeded")
    seniority: str = ""
    is_leadership: bool = Field(default=False, alias="isLeadership")


class TeamSelectionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    required_roles: list[str] = Field(default_factory=list, alias="requiredRoles")
    role_requirements: list[RequiredTeamRole] = Field(
        default_factory=list, alias="roleRequirements"
    )
    members: list[TeamMemberSelection] = Field(default_factory=list)


class EvidenceCandidate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    snippet: str = ""
    source: str = ""


class EvidenceScore(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    score: float = 0.0
    rationale: str = ""


class EvidenceSelectionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    candidates_considered: int = Field(default=0, alias="candidatesConsidered")
    selected_studies: list[str] = Field(default_factory=list, alias="selectedStudies")
    scores: list[EvidenceScore] = Field(default_factory=list)
