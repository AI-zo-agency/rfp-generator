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
