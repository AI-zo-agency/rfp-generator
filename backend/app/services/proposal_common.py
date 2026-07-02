"""Shared proposal helpers — kept separate to avoid circular imports."""

from __future__ import annotations

import asyncio

from app.models.rfp import RfpRecord
from app.services.go_no_go_service import RfpContentInfo, _assess_rfp_content, _build_rfp_context
from app.services.rfp_repository import get_rfp


class ProposalError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def can_start_proposal(rfp: RfpRecord) -> bool:
    return rfp.go_no_go in {"go", "review"}


def load_rfp_for_proposal(rfp_id: str) -> tuple[RfpRecord, RfpContentInfo, str]:
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)
    if not can_start_proposal(rfp):
        raise ProposalError(
            "RFP must be marked Go or Go With Conditions before generating sections.",
            status_code=400,
        )
    content = _assess_rfp_content(rfp)
    rfp_context = _build_rfp_context(rfp, content)
    if content.substantive_chars < 200:
        raise ProposalError(
            "Insufficient RFP content. Upload a PDF or add a description.",
            status_code=400,
        )
    return rfp, content, rfp_context


async def aload_rfp_for_proposal(rfp_id: str) -> tuple[RfpRecord, RfpContentInfo, str]:
    """Load RFP + PDF text off the event loop (Supabase PDF fetch is blocking)."""
    return await asyncio.to_thread(load_rfp_for_proposal, rfp_id)
