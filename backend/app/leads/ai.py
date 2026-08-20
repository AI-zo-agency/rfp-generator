"""AI layer for the Wave 3 PoC — runs on the existing OpenRouter plumbing.

Two jobs, both firmly on the intelligence side of the Wave 3 charter:

  1. enrich_company  — stand-in for Apollo (phase 3). Infers firmographics
     from an email domain when HubSpot has no company record.
  2. synthesize_brief — turns the scored facts into a prep summary and the
     open questions a human should resolve (phase 7).

Neither writes outreach copy. The charter is explicit that the system "does
not send emails, generate copy, or automate relationships", so there is no
message-drafting function here on purpose.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm import chat_json, is_configured

logger = logging.getLogger(__name__)

# ponytail: process-local cache, cleared on restart. A PoC re-reads the same
# dozen contacts constantly and these calls cost money. Redis if it outgrows one process.
_CACHE: dict[str, dict[str, Any]] = {}

ENRICH_SYSTEM = """You infer B2B firmographics from an email domain for a \
marketing agency's CRM. You are replacing a paid enrichment API, so accuracy \
and calibrated confidence matter more than completeness.

Return JSON only:
{
  "company_name": string|null,
  "industry": string|null,
  "city": string|null,
  "state": string|null,     // 2-letter US state code
  "employee_band": string|null,   // e.g. "1-10", "11-50", "51-200", "201-1000", "1000+"
  "what_they_do": string|null,    // one sentence
  "confidence": "high"|"medium"|"low",
  "basis": string   // how you concluded this, or why you could not
}

Rules:
- If you do not recognize the domain, say so: nulls and confidence "low".
- Never invent a city or headcount to fill the field. null is a valid answer.
- "confidence": high only for organizations you actually recognize."""

BRIEF_SYSTEM = """You write pre-call prep notes for an agency's business \
development team. Your job is research synthesis, NOT sales copy.

Return JSON only:
{
  "summary": string,          // 2-3 sentences: who this is, why they scored where they did
  "open_questions": [string], // 2-4 things the rep should find out or verify before reaching out
  "watch_outs": [string]      // 0-3 risks in the data itself, e.g. unverified fields, stale activity
}

Hard rules:
- Do NOT write email copy, subject lines, openers, pitches, or suggested phrasing.
- Do NOT invent facts. Work only from the record given. If a field is marked
  inferred or unverified, treat it as unverified and say so in watch_outs.
- The reader decides whether to reach out. You only prepare them."""


def available() -> bool:
    return is_configured()


async def enrich_company(domain: str, sample_email: str) -> dict[str, Any]:
    """Phase 3 stand-in: infer firmographics from a domain. Always AI-guessed."""
    key = f"enrich:{domain}"
    if key in _CACHE:
        return _CACHE[key]

    payload, _model = await chat_json(
        [
            {"role": "system", "content": ENRICH_SYSTEM},
            {
                "role": "user",
                "content": f"Email domain: {domain}\nExample address at this domain: {sample_email}",
            },
        ],
        tier="light",
        temperature=0.0,
        node_name="leads_enrich",
    )
    result = {**payload, "source": "ai-inferred", "domain": domain}
    _CACHE[key] = result
    return result


def preparation_facts(brief: dict[str, Any], enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the verified and inferred facts the preparation model may use."""
    enrichment = enrichment or {}
    company_fields = (
        "company_name", "industry", "company_type", "city", "state", "employee_band",
        "employee_count", "founded", "inferred_revenue", "website", "what_they_do", "tags",
        "confidence", "basis", "source",
    )
    person_fields = (
        "full_name", "job_title", "job_title_role", "job_title_levels", "job_company_name",
        "phone", "linkedin_url", "confidence", "basis", "source",
    )
    person = enrichment.get("person") if isinstance(enrichment.get("person"), dict) else {}
    return {
        "who": brief.get("who"),
        "company": brief.get("company"),
        "industry": brief.get("industry"),
        "location": brief.get("location"),
        "owner": brief.get("owner"),
        "score": brief.get("score"),
        "band": brief.get("band"),
        "score_breakdown": brief.get("score_breakdown"),
        "why_scored": brief.get("why"),
        "relevant_case_studies": brief.get("case_studies"),
        "firmographics_verified": brief.get("company_data_source") == "hubspot",
        "monid_company": {key: enrichment[key] for key in company_fields if enrichment.get(key) is not None},
        "monid_contact": {key: person[key] for key in person_fields if person.get(key) is not None},
        "enrichment_status": {
            "company_error": enrichment.get("company_error"),
            "person_error": enrichment.get("person_error"),
            "company_skipped": enrichment.get("company_skipped"),
        },
        "website_visitor_intel": "not available — visitor tracking not deployed",
    }


async def synthesize_brief(brief: dict[str, Any], enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Phase 7: prep summary and open questions. No messaging."""
    facts = preparation_facts(brief, enrichment)
    key = f"brief:{json.dumps(facts, sort_keys=True, default=str)}"
    if key in _CACHE:
        return _CACHE[key]

    payload, _model = await chat_json(
        [
            {"role": "system", "content": BRIEF_SYSTEM},
            {"role": "user", "content": _render(facts)},
        ],
        tier="heavy",
        temperature=0.3,
        node_name="leads_brief",
    )
    result = {
        "summary": payload.get("summary"),
        "open_questions": payload.get("open_questions", []),
        "watch_outs": payload.get("watch_outs", []),
    }
    _CACHE[key] = result
    return result


def _render(facts: dict[str, Any]) -> str:
    lines = []
    for key, value in facts.items():
        if value in (None, [], {}):
            value = "unknown"
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
