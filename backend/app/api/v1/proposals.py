from fastapi import APIRouter, HTTPException

from app.models.proposal import (
    ProposalDraft,
    ProposalGenerateResponse,
    ProposalPhase2Response,
    ProposalPhase3Response,
    ProposalResearchCache,
    ProposalSectionImproveResponse,
    SectionImproveRequest,
)
from app.services.proposal_generator import (
    ProposalError,
    generate_full_proposal,
    generate_sections_1_3,
    run_phase2_retrieval,
    run_phase3_drafting,
)
from app.services.proposal_section_editor import improve_proposal_section
from app.services.proposal_repository import get_proposal_draft, get_research_cache, save_proposal_draft
from app.services.rfp_repository import get_rfp

router = APIRouter(prefix="/rfps", tags=["proposals"])


@router.get("/{rfp_id}/proposal")
def get_proposal(rfp_id: str) -> dict[str, object]:
    if not get_rfp(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    draft = get_proposal_draft(rfp_id)
    research = get_research_cache(rfp_id)
    return {
        "draft": draft.model_dump(by_alias=True) if draft else None,
        "research": research.model_dump(by_alias=True) if research else None,
    }


@router.put("/{rfp_id}/proposal")
def upsert_proposal(rfp_id: str, draft: ProposalDraft) -> dict[str, object]:
    if not get_rfp(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    if draft.rfp_id != rfp_id:
        raise HTTPException(status_code=400, detail="rfpId mismatch")
    save_proposal_draft(draft)
    return {"ok": True, "draft": draft.model_dump(by_alias=True)}


@router.post("/{rfp_id}/proposal/generate", response_model=ProposalGenerateResponse)
async def generate_proposal_endpoint(rfp_id: str) -> ProposalGenerateResponse:
    """Generate full proposal: static Sections 1–3 + RFP-mapped sections from evidence."""
    try:
        draft, brand_voice, research = await generate_full_proposal(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Full proposal generation failed: {exc}",
        ) from exc

    return ProposalGenerateResponse(draft=draft, brandVoice=brand_voice, research=research)


@router.post(
    "/{rfp_id}/proposal/generate/full",
    response_model=ProposalGenerateResponse,
)
async def generate_full_proposal_endpoint(rfp_id: str) -> ProposalGenerateResponse:
    """Same as POST /generate — static Sections 1–3 then RFP-varying sections."""
    try:
        draft, brand_voice, research = await generate_full_proposal(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Full proposal generation failed: {exc}",
        ) from exc

    return ProposalGenerateResponse(draft=draft, brandVoice=brand_voice, research=research)


@router.post(
    "/{rfp_id}/proposal/generate/sections-1-3",
    response_model=ProposalGenerateResponse,
)
async def generate_sections_1_3_endpoint(rfp_id: str) -> ProposalGenerateResponse:
    """Generate static Sections 1–3 only (Phase 2 retrieval is a separate endpoint)."""
    try:
        draft, brand_voice, research = await generate_sections_1_3(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sections 1–3 generation failed: {exc}",
        ) from exc

    return ProposalGenerateResponse(draft=draft, brandVoice=brand_voice, research=research)


@router.post(
    "/{rfp_id}/proposal/phase-2-retrieval",
    response_model=ProposalPhase2Response,
)
async def phase2_retrieval_endpoint(rfp_id: str) -> ProposalPhase2Response:
    """Phase 2 only: map RFP sections, per-section Supermemory retrieval, coverage, evidence corpus."""
    try:
        research = await run_phase2_retrieval(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Phase 2 retrieval failed: {exc}",
        ) from exc

    return ProposalPhase2Response(research=research)


@router.post(
    "/{rfp_id}/proposal/phase-3-drafting",
    response_model=ProposalPhase3Response,
)
async def phase3_drafting_endpoint(rfp_id: str) -> ProposalPhase3Response:
    """Phase 3: draft all RFP sections from evidence corpus with [E#] citations."""
    try:
        draft, research = await run_phase3_drafting(rfp_id)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Phase 3 drafting failed: {exc}",
        ) from exc

    return ProposalPhase3Response(draft=draft, research=research)


@router.post(
    "/{rfp_id}/proposal/sections/{section_id}/improve",
    response_model=ProposalSectionImproveResponse,
)
async def improve_section_endpoint(
    rfp_id: str,
    section_id: str,
    body: SectionImproveRequest,
) -> ProposalSectionImproveResponse:
    """Re-query KB with new detailed queries and re-draft one section from user feedback."""
    try:
        section, draft, research, _provider, assistant_message = await improve_proposal_section(
            rfp_id,
            section_id,
            body.message,
        )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Section improve failed: {exc}",
        ) from exc

    return ProposalSectionImproveResponse(
        section=section,
        draft=draft,
        research=research,
        assistantMessage=assistant_message,
    )
