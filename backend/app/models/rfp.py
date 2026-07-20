from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RfpStage = Literal[
    "intake",
    "go_no_go",
    "compliance",
    "sections_1_3",
    "sections_4_5",
    "pricing",
    "review",
    "export",
    "submitted",
    "won",
    "lost",
    "passed",
]

RfpStatus = Literal[
    "new",
    "active",
    "pending_approval",
    "in_progress",
    "review",
    "submitted",
    "won",
    "lost",
    "passed",
]

GoNoGoRecommendation = Literal["go", "no_go", "review"] | None
RfpPriority = Literal["critical", "high", "medium", "low"]
RfpSource = Literal["justwin", "manual"]
ContractRole = Literal["prime", "subconsultant"]
JustWinTab = Literal["hot", "warm", "review"]


class RfpRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    title: str
    client: str
    source: RfpSource = "justwin"
    external_id: str | None = Field(default=None, alias="externalId")
    sector: str = "Public Sector"
    location: str = ""
    due_date: str = Field(alias="dueDate")
    received_date: str = Field(alias="receivedDate")
    stage: RfpStage = "intake"
    status: RfpStatus = "new"
    priority: RfpPriority = "medium"
    fit_score: int | None = Field(default=None, alias="fitScore")
    worth_score: int | None = Field(default=None, alias="worthScore")
    go_no_go: GoNoGoRecommendation = Field(default=None, alias="goNoGo")
    assigned_to: str | None = Field(default=None, alias="assignedTo")
    estimated_value: int | None = Field(default=None, alias="estimatedValue")
    page_limit: int | None = Field(default=None, alias="pageLimit")
    last_activity: str = Field(alias="lastActivity")
    last_activity_note: str = Field(alias="lastActivityNote")
    contract_role: ContractRole = Field(default="prime", alias="contractRole")
    pdf_url: str | None = Field(default=None, alias="pdfUrl")
    pdf_path: str | None = Field(default=None, alias="pdfPath")
    description: str | None = None
    justwin_tab: JustWinTab | None = Field(default=None, alias="justwinTab")
    justwin_detail_url: str | None = Field(default=None, alias="justwinDetailUrl")
    synced_at: str | None = Field(default=None, alias="syncedAt")
    go_no_go_analysis: dict | None = Field(default=None, alias="goNoGoAnalysis")
    # Enriched from proposal draft — not a DB column on rfps
    google_doc_url: str | None = Field(default=None, alias="googleDocUrl")


class ManualRfpCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    client: str
    location: str = ""
    sector: str = "Public Sector"
    due_date: str = Field(alias="dueDate")
    description: str | None = None
    page_limit: int | None = Field(default=None, alias="pageLimit")
    estimated_value: int | None = Field(default=None, alias="estimatedValue")
    priority: RfpPriority = "medium"


class DashboardStats(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_rfps: int = Field(alias="activeRfps")
    pending_go_no_go: int = Field(alias="pendingGoNoGo")
    in_progress: int = Field(alias="inProgress")
    due_this_week: int = Field(alias="dueThisWeek")
    submitted_this_month: int = Field(alias="submittedThisMonth")
    win_rate: int = Field(alias="winRate")
    pipeline_value: int = Field(alias="pipelineValue")
    avg_fit_score: int = Field(alias="avgFitScore")


class DashboardResponse(BaseModel):
    rfps: list[RfpRecord]
    all_rfps: list[RfpRecord] = Field(alias="allRfps")
    stats: DashboardStats


class HealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    app: str
    database: str = Field(description="supabase or sqlite")
    database_path: str | None = Field(default=None, alias="databasePath")
