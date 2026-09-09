"""Deterministic pre-Stage-3 pricing contract (commission vs fee structure)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FeeModel = Literal["hourly", "phased_fee", "commission", "hybrid", "unknown"]
ContractConfidence = Literal["low", "medium", "high"]


class PricingContract(BaseModel):
    """Locked pricing shape extracted before the Stage 3 budget LLM call."""

    model_config = ConfigDict(populate_by_name=True)

    fee_model: FeeModel = Field(default="unknown", alias="feeModel")
    media_spend_annual: float | None = Field(default=None, alias="mediaSpendAnnual")
    commission_rate: float | None = Field(
        default=None,
        alias="commissionRate",
        description="Fraction 0–1 when known; never invented.",
    )
    must_disclose_media_compensation: bool = Field(
        default=False,
        alias="mustDiscloseMediaCompensation",
        description=(
            "RFP requires Cost to state commission / markup / pass-through treatment "
            "even when feeModel is not commission (flat/phased planning fee)."
        ),
    )
    evidence_notes: list[str] = Field(default_factory=list, alias="evidenceNotes")
    confidence: ContractConfidence = "low"
