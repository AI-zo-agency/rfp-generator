"""Wave 3 PoC endpoints — Lead Finder & Outreach Matcher.

Static fixture for the contact list; AI (OpenRouter) for enrichment and
brief synthesis. See app/leads/scoring.py and app/leads/ai.py for the
swap points once HubSpot/Apollo credentials land.
"""

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from app.leads import ai
from app.leads import case_studies
from app.leads import monid
from app.leads.scoring import (
    WEIGHTS_RATIONALE,
    build_brief,
    build_leads,
    email_domain,
    load_dataset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("")
def list_leads() -> dict:
    data = load_dataset()
    leads = build_leads(data)
    scored = [lead for lead in leads if not lead.disqualified_reason]
    return {
        "source": "static-fixture",
        "rationale": WEIGHTS_RATIONALE,
        "stats": {
            "total": len(leads),
            "scored": len(scored),
            "disqualified": len(leads) - len(scored),
            "hot": sum(1 for lead in scored if lead.band == "Hot"),
            "warm": sum(1 for lead in scored if lead.band == "Warm"),
            "cool": sum(1 for lead in scored if lead.band == "Cool"),
        },
        "leads": [
            {
                "id": lead.contact["id"],
                "name": lead.contact.get("name"),
                "email": lead.contact["email"],
                "owner": lead.contact.get("owner"),
                "company": (lead.company or {}).get("name"),
                "industry": (lead.company or {}).get("industry"),
                "location": ", ".join(
                    p for p in ((lead.company or {}).get("city"), (lead.company or {}).get("state")) if p
                ) or None,
                "last_activity": lead.contact.get("last_activity"),
                "score": lead.score,
                "band": lead.band,
                "breakdown": lead.breakdown,
                "reasons": lead.reasons,
                "disqualified_reason": lead.disqualified_reason,
            }
            for lead in leads
        ],
    }


def _find_lead(contact_id: str):
    data = load_dataset()
    for lead in build_leads(data):
        if lead.contact["id"] == contact_id:
            if lead.disqualified_reason:
                raise HTTPException(
                    status_code=409,
                    detail=f"Contact is disqualified: {lead.disqualified_reason}",
                )
            return data, lead
    raise HTTPException(status_code=404, detail="Contact not found")


async def _build_brief(contact_id: str) -> dict:
    data, lead = _find_lead(contact_id)
    brief = build_brief(lead, data.get("case_studies", {}))
    kb_studies = await case_studies.find_case_studies(brief.get("industry"))
    if kb_studies is not None:
        brief["case_studies"] = kb_studies
        brief["case_studies_source"] = "supermemory"
    else:
        brief["case_studies_source"] = "fixture" if brief.get("case_studies") else "none"
    brief["ai_available"] = ai.available()
    return brief


@router.get("/{contact_id}/brief")
async def get_brief(
    contact_id: str,
    ai_summary: bool = Query(False, alias="ai"),
) -> dict:
    brief = await _build_brief(contact_id)
    if ai_summary and brief["ai_available"]:
        try:
            brief["ai"] = await ai.synthesize_brief(brief)
        except Exception as exc:  # PoC: a failed summary must not kill the brief
            logger.warning("AI brief synthesis failed for %s: %s", contact_id, exc)
            brief["ai_error"] = str(exc)
    return brief


@router.post("/{contact_id}/brief")
async def generate_brief(
    contact_id: str,
    enrichment: dict[str, Any] | None = Body(default=None),
) -> dict:
    brief = await _build_brief(contact_id)
    if not brief["ai_available"]:
        raise HTTPException(status_code=503, detail="AI preparation is not configured")
    try:
        brief["ai"] = await ai.synthesize_brief(brief, enrichment)
    except Exception as exc:
        logger.warning("AI brief synthesis failed for %s: %s", contact_id, exc)
        raise HTTPException(status_code=502, detail="Could not generate preparation notes") from exc
    return brief


@router.post("/{contact_id}/enrich")
async def enrich(contact_id: str) -> dict:
    """Enrich company + person from Monid. HubSpot company records are not overwritten."""
    _data, lead = _find_lead(contact_id)
    email = lead.contact["email"]
    domain = email_domain(email)
    skip_company = (lead.company or {}).get("source") == "hubspot"
    if monid.available():
        result = await monid.enrich_contact(
            domain,
            email,
            skip_company=skip_company,
            known_company=(lead.company or {}).get("name"),
        )
        if result.get("company_name") or result.get("person"):
            return result
        errors = " ".join(
            part for part in (result.get("company_error"), result.get("person_error")) if part
        )
        if monid.is_payment_error(errors):
            raise HTTPException(
                status_code=402,
                detail="Monid wallet has insufficient balance. Add funds at https://app.monid.ai",
            )
        if "No records" in errors:
            raise HTTPException(status_code=404, detail="Monid found no matching company or person record")
        logger.warning("Monid enrichment returned nothing for %s: %s", contact_id, errors)
        raise HTTPException(status_code=502, detail="Monid enrichment failed; try again later")
    if skip_company:
        raise HTTPException(
            status_code=409,
            detail="Company already has a verified HubSpot record — not overwriting it",
        )
    if ai.available():
        return await ai.enrich_company(domain, email)
    raise HTTPException(status_code=503, detail="Monid and AI enrichment are not configured")
