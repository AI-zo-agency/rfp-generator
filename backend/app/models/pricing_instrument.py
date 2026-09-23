"""Typed buyer pricing instrument extracted from RFP cost/forms."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PricingInstrumentKind = Literal[
    "buyer_pricing_form",
    "personnel_loading",
    "phased_fee_schedule",
    "none",
]
IdentityValueSource = Literal["companyfacts", "extract", "manual"]


class InstrumentEvidence(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    field: str
    quote: str
    locator: str = ""


class IdentityField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    label: str
    value_source: IdentityValueSource = Field(alias="valueSource")


class PricingTrack(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    nte_annual: float | None = Field(default=None, alias="nteAnnual")
    asks_hourly: bool = Field(default=False, alias="asksHourly")
    asks_hours: bool = Field(default=False, alias="asksHours")
    rate: float | None = None
    hours: float | None = None


class PricingSignature(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    printed_name_required: bool = Field(default=False, alias="printedNameRequired")
    title_required: bool = Field(default=False, alias="titleRequired")
    signature_required: bool = Field(default=False, alias="signatureRequired")
    date_required: bool = Field(default=False, alias="dateRequired")


class PricingInstrument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: PricingInstrumentKind
    bid_number: str | None = Field(default=None, alias="bidNumber")
    identity_fields: list[IdentityField] = Field(default_factory=list, alias="identityFields")
    tracks: list[PricingTrack] = Field(default_factory=list)
    signature: PricingSignature | None = None
    evidence: list[InstrumentEvidence] = Field(default_factory=list)
    confidence: float = 0.0
    internal_note_rules: list[str] = Field(default_factory=list, alias="internalNoteRules")
