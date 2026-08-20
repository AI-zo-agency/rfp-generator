"""Wave 3 PoC endpoints — Lead Finder & Outreach Matcher.

Static fixture for the contact list; AI (OpenRouter) for enrichment and
brief synthesis. See app/leads/scoring.py and app/leads/ai.py for the
swap points once HubSpot/Apollo credentials land.
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.leads import ai
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


@router.get("/{contact_id}/brief")
async def get_brief(
    contact_id: str,
    ai_summary: bool = Query(False, alias="ai"),
) -> dict:
    data, lead = _find_lead(contact_id)
    brief = build_brief(lead, data.get("case_studies", {}))
    brief["ai_available"] = ai.available()
    if ai_summary and brief["ai_available"]:
        try:
            brief["ai"] = await ai.synthesize_brief(brief)
        except Exception as exc:  # PoC: a failed summary must not kill the brief
            logger.warning("AI brief synthesis failed for %s: %s", contact_id, exc)
            brief["ai_error"] = str(exc)
    return brief


@router.post("/{contact_id}/enrich")
async def enrich(contact_id: str) -> dict:
    """Apollo stand-in. Infers firmographics from the email domain via LLM.

    Everything it returns is a model guess, labelled as such. It is offered
    only where HubSpot has no verified company record.
    """
    if not ai.available():
        raise HTTPException(status_code=503, detail="No LLM provider configured")
    _data, lead = _find_lead(contact_id)
    if (lead.company or {}).get("source") == "hubspot":
        raise HTTPException(
            status_code=409,
            detail="Company already has a verified HubSpot record — not overwriting it",
        )
    email = lead.contact["email"]
    return await ai.enrich_company(email_domain(email), email)
