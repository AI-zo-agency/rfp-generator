"""Cite-linked supplemental PDF attached beside the primary RFP solicitation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RfpPacketFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    rfp_id: str = Field(alias="rfpId")
    cite_target: str = Field(alias="citeTarget")
    cite_quote: str = Field(default="", alias="citeQuote")
    filename: str = ""
    storage_path: str = Field(alias="storagePath")
    content_sha256: str = Field(default="", alias="contentSha256")
    uploaded_at: str = Field(alias="uploadedAt")
    uploaded_by: str | None = Field(default=None, alias="uploadedBy")
