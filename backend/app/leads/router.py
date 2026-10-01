"""Wave 3 endpoints — Lead Finder & Outreach Matcher.

Contacts come from the read-only HubSpot mirror (app/leads/hubspot.py) once it
is configured and synced, else the static fixture. AI (OpenRouter) for
enrichment fallback and brief synthesis.
"""

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from app.leads import ai
from app.leads import case_studies
from app.leads import hubspot
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


def _dataset() -> dict[str, Any]:
    """HubSpot mirror when set up, else the fixture. A broken mirror is an error,
    never a silent fall back to fixture data dressed up as real contacts."""
    fixture = load_dataset()
    try:
        mirror = hubspot.load_mirror_dataset()
    except Exception as exc:
        logger.error("operation=leads_dataset status=mirror_failed error=%s", exc)
        raise HTTPException(status_code=503, detail="HubSpot contact mirror is unavailable") from exc
    if mirror is None:
        return {**fixture, "source": "static-fixture"}
    # Case-study titles are zö's own, not HubSpot's; keep them as the Supermemory fallback.
    return {**mirror, "case_studies": fixture.get("case_studies", {})}


class HubSpotSyncBody(BaseModel):
    mode: str = "auto"


@router.post("/hubspot/sync")
def hubspot_sync(payload: HubSpotSyncBody | None = None) -> dict:
    """Refresh the HubSpot mirror: the scheduler (X-Cron-Secret) or the Lead Finder's
    Refresh button (signed-in user). Both are checked by app/api/auth_guard.py."""
    mode = payload.mode if payload else "auto"
    if mode not in ("auto", "full"):
        raise HTTPException(status_code=422, detail="mode must be 'auto' or 'full'")
    if not hubspot.configured():
        raise HTTPException(status_code=503, detail="HUBSPOT_API_KEY is not set")
    try:
        return hubspot.run_sync(mode)
    except hubspot.HubSpotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("")
def list_leads() -> dict:
    data = _dataset()
    leads = build_leads(data)
    scored = [lead for lead in leads if not lead.disqualified_reason]
    return {
        "source": data["source"],
        "synced_at": data.get("synced_at"),
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
    data = _dataset()
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
    try:
        from app.services.user_activity import emit_activity

        label = brief.get("who") or brief.get("company") or contact_id
        emit_activity(
            workspace="leads",
            action="prep.generated",
            summary=f"Generated AI preparation notes for {label}",
            entity_type="lead",
            entity_id=contact_id,
            entity_label=str(label)[:300],
        )
    except Exception:  # noqa: BLE001
        pass
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
            try:
                from app.services.user_activity import emit_activity

                label = (
                    result.get("company_name")
                    or (lead.company or {}).get("name")
                    or lead.contact.get("name")
                    or contact_id
                )
                emit_activity(
                    workspace="leads",
                    action="enrich.monid_ran",
                    summary=f"Enriched prospect via Monid: {label}",
                    entity_type="lead",
                    entity_id=contact_id,
                    entity_label=str(label)[:300],
                )
            except Exception:  # noqa: BLE001
                pass
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
