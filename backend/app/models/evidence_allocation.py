"""Evidence allocation ledger — who owns reusable assets (W6 / T6.2)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AllocationAssetClass(str, Enum):
    CASE_STUDY = "case_study"
    PROOF_POINT = "proof_point"
    BOILERPLATE = "boilerplate"
    HIGH_RISK_NUMERIC_CLAIM = "high_risk_numeric_claim"


class AllocationEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    asset_id: str = Field(alias="assetId")
    asset_class: AllocationAssetClass = Field(alias="assetClass")
    owner_section_id: str = Field(alias="ownerSectionId")
    reference_only_section_ids: list[str] = Field(
        default_factory=list, alias="referenceOnlySectionIds"
    )
    excluded_section_ids: list[str] = Field(
        default_factory=list, alias="excludedSectionIds"
    )
    rationale: str = ""
    fingerprint: str = ""


class EvidenceAllocationLedger(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str
    entries: list[AllocationEntry] = Field(default_factory=list)
