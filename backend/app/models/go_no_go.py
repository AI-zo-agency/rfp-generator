from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GoNoGoRecommendation = Literal["go", "no_go", "review"]

DECISION_MATRIX_DIMENSIONS = [
    "Technical Capability Match",
    "Resource Availability",
    "Financial Viability",
    "Strategic Value",
    "Win Probability",
]


class GoNoGoDecisionMatrixRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dimension: str
    score: int = Field(ge=0, le=5)
    notes: str = ""


CapabilityStatus = Literal["verified", "partial", "gap", "unverified"]


class GoNoGoCapabilityRow(BaseModel):
    """One RFP requirement matched (or not) to KB evidence.

    Previously these rows existed only as free-form Markdown inside
    stageOneReport, so nothing could check a "Verified" claim against the KB.
    Structured here so validation is deterministic: a row may keep
    status="verified" only when kb_source names a document that was actually
    retrieved for this RFP and that document's text supports the claim.
    """

    model_config = ConfigDict(populate_by_name=True)

    requirement: str
    status: CapabilityStatus = "unverified"
    # KB document/file name backing a verified claim. Required for "verified".
    kb_source: str = Field(default="", alias="kbSource")
    evidence: str = ""
    # True when the RFP treats this requirement as mandatory or scored.
    is_core: bool = Field(default=False, alias="isCore")
    # service|role|technical|compliance|logistics — Technical Capability score
    # uses craft/platform categories; role/logistics feed Resource Availability.
    category: str = "service"
    # Set by the validator when a claim fails its citation check.
    downgrade_reason: str = Field(default="", alias="downgradeReason")
    # Why a gap is a gap. "absent" = nothing in the KB addresses it.
    # "contradicted" = the KB explicitly disclaims it (a bio reading
    # "Web Design/Development (Not Programming)"). "adjacent" = the KB has
    # related but materially different work. These are different findings:
    # absent may be fixable by re-ingesting; contradicted never is.
    evidence_state: str = Field(default="", alias="evidenceState")


class GoNoGoFlag(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    category: str
    severity: Literal["info", "warning", "critical"] = "warning"
    message: str


class GoNoGoDimension(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    summary: str
    score_impact: str = Field(alias="scoreImpact")
    flags: list[GoNoGoFlag] = Field(default_factory=list)


class GoNoGoEvaluation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    question: str
    answer: str
    impact: str = ""


class GoNoGoDeadlineInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    today: str
    due_date: str | None = Field(default=None, alias="dueDate")
    days_remaining: int | None = Field(default=None, alias="daysRemaining")
    is_past: bool = Field(default=False, alias="isPast")
    is_today: bool = Field(default=False, alias="isToday")
    late_submission_disqualifies: bool = Field(
        default=False, alias="lateSubmissionDisqualifies"
    )
    note: str = ""


class GoNoGoAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fit_score: int | None = Field(default=None, alias="fitScore", ge=0, le=5)
    worth_score: int | None = Field(default=None, alias="worthScore", ge=0, le=5)
    recommendation: GoNoGoRecommendation | None = None
    insufficient_data: bool = Field(default=False, alias="insufficientData")
    summary: str
    scope_match: GoNoGoDimension = Field(alias="scopeMatch")
    sector_match: GoNoGoDimension = Field(alias="sectorMatch")
    compliance: GoNoGoDimension
    team_match: GoNoGoDimension = Field(alias="teamMatch")
    evaluations: list[GoNoGoEvaluation] = Field(default_factory=list)
    critical_gaps: list[str] = Field(default_factory=list, alias="criticalGaps")
    conditions: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(
        default_factory=list, alias="clarifyingQuestions"
    )
    stage_one_report: str = Field(default="", alias="stageOneReport")
    capability_matrix: list[GoNoGoCapabilityRow] = Field(
        default_factory=list, alias="capabilityMatrix"
    )
    decision_matrix: list[GoNoGoDecisionMatrixRow] = Field(
        default_factory=list, alias="decisionMatrix"
    )
    deadline: GoNoGoDeadlineInfo | None = None
    action_flags: list[str] = Field(default_factory=list, alias="actionFlags")
    provider: str | None = None
