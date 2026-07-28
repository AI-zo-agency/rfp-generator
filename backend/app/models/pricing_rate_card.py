"""Typed pricing rate card extracted from KB (00_Guide_Pricing)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PricingRate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rate_id: str = Field(alias="rateId")
    service: str
    tier: str = "Average"
    unit: Literal["hour", "fixed", "percent", "monthly", "annual", "unknown"] = "fixed"
    amount: float | None = None
    amount_low: float | None = Field(default=None, alias="amountLow")
    amount_high: float | None = Field(default=None, alias="amountHigh")
    menu_id: str = Field(default="", alias="menuId")
    source_doc: str = Field(default="00_Guide_Pricing", alias="sourceDoc")
    confidence: float = 1.0
    notes: str = ""


class PricingRateCard(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str = "pricing-rate-card-v1"
    source: str = "supermemory_kb"
    rates: list[PricingRate] = Field(default_factory=list)
    guide_excerpt_chars: int = Field(default=0, alias="guideExcerptChars")
    built_at: str = Field(default="", alias="builtAt")
    warnings: list[str] = Field(default_factory=list)
