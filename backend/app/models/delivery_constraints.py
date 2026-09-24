"""Hard delivery facts for SOW, Timeline, and Cost track alignment."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.pricing_instrument import InstrumentEvidence


class DeliveryTrack(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    nte_annual: float | None = Field(default=None, alias="nteAnnual")
    billing: str = ""


class DeliveryHorizon(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_term: str = Field(default="", alias="baseTerm")
    renewals: str = ""
    max_term: str = Field(default="", alias="maxTerm")


class DeliveryConstraints(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tracks: list[DeliveryTrack] = Field(default_factory=list)
    mandatory_deliverables: list[str] = Field(default_factory=list, alias="mandatoryDeliverables")
    bidder_proposes: list[str] = Field(default_factory=list, alias="bidderProposes")
    out_of_scope: list[str] = Field(default_factory=list, alias="outOfScope")
    horizon: DeliveryHorizon | None = None
    non_commingle_tracks: bool = Field(default=False, alias="nonCommingleTracks")
    # Draft Agreement / contract: work product belongs to the buyer (exclusive).
    buyer_owns_deliverables: bool = Field(default=False, alias="buyerOwnsDeliverables")
    # On-call / task-order RFQs that gate work behind a Letter Proposal / task order.
    letter_proposal_gate: bool = Field(default=False, alias="letterProposalGate")
    evidence: list[InstrumentEvidence] = Field(default_factory=list)
