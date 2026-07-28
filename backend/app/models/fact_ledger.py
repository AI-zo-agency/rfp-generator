"""Canonical Fact Ledger — typed cross-section claims (W4 / T4.1)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClaimClass(str, Enum):
    BUDGET = "budget"
    YEARS_EXPERIENCE = "years_experience"
    EMPLOYEE_COUNT = "employee_count"
    CERTIFICATION = "certification"
    DATE = "date"
    CONTRACT_VALUE = "contract_value"
    RETENTION_STAT = "retention_stat"


class LedgerClaim(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    claim_id: str = Field(alias="claimId")
    claim_class: ClaimClass = Field(alias="claimClass")
    subject_type: Literal["person", "company", "client", "budget"] = Field(
        alias="subjectType"
    )
    subject_id: str = Field(alias="subjectId")
    field_name: str = Field(alias="fieldName")
    value_text: str = Field(alias="valueText")
    value_number: float | None = Field(default=None, alias="valueNumber")
    unit: str | None = None
    source_doc: str = Field(default="", alias="sourceDoc")
    source_locator: str = Field(default="", alias="sourceLocator")
    verbatim_snippet: str = Field(default="", alias="verbatimSnippet")
    confidence: float = 1.0
    conflict_group: str | None = Field(default=None, alias="conflictGroup")


class LedgerPerson(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    person_id: str = Field(alias="personId")
    name: str
    title: str | None = None
    claims: list[str] = Field(default_factory=list)  # claim_ids


class LedgerCompany(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    founded_date: str | None = Field(default=None, alias="foundedDate")
    employee_count_claim_id: str | None = Field(
        default=None, alias="employeeCountClaimId"
    )
    certification_claim_ids: list[str] = Field(
        default_factory=list, alias="certificationClaimIds"
    )


class LedgerClient(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="clientId")
    name: str
    public_flag: bool | None = Field(default=None, alias="publicFlag")
    sector: str | None = None


class FactLedgerOverride(BaseModel):
    """Human-approved authoritative value for a claim key (KB may still disagree)."""

    model_config = ConfigDict(populate_by_name=True)

    subject_id: str = Field(alias="subjectId")
    claim_class: ClaimClass = Field(alias="claimClass")
    field_name: str = Field(alias="fieldName")
    value_text: str = Field(alias="valueText")
    value_number: float | None = Field(default=None, alias="valueNumber")
    unit: str | None = None
    subject_type: Literal["person", "company", "client", "budget"] = Field(
        default="person", alias="subjectType"
    )
    reason: str = ""
    approved_by: str = Field(default="", alias="approvedBy")


class FactLedger(BaseModel):
    """Versioned ledger of typed claims used for cross-section consistency."""

    model_config = ConfigDict(populate_by_name=True)

    version: str
    built_at: str = Field(alias="builtAt")
    people: list[LedgerPerson] = Field(default_factory=list)
    company: LedgerCompany = Field(default_factory=LedgerCompany)
    clients: list[LedgerClient] = Field(default_factory=list)
    claims: list[LedgerClaim] = Field(default_factory=list)
    blocking_conflicts: list[str] = Field(
        default_factory=list, alias="blockingConflicts"
    )
    resolution_notes: list[str] = Field(
        default_factory=list,
        alias="resolutionNotes",
        description="Audit trail of override resolutions applied at build time.",
    )
