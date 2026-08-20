"""Lead prioritization for Wave 3 (Lead Finder & Outreach Matcher) — PoC.

Pure functions over a static fixture. No HubSpot API, no Apollo, no RB2B.
Swap `load_dataset` for real connectors once credentials land; nothing else
in this module needs to change.

Scoring weights are derived from what zö's HubSpot actually contains
(see WEIGHTS_RATIONALE) and are meant to be argued with, not trusted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "leads_poc.json"

WEIGHTS_RATIONALE = """\
Industry tiers come from the sector mix visible in zö's own company records:
of 22 companies on page 1, 8 were Paper & Forest Products, 4 Libraries,
4 Higher Education, 3 Hospital & Health Care, 3 Telecommunications.
Government scores as a distinct tier because it is absent from the company
list but heavy in the contact list (City of Sacramento, Placer County) and
because the agency mind map calls out municipal work explicitly.

Geography follows the same evidence: OR and WA dominate every city column,
with a Northern California cluster second.

There is no title/seniority field in this HubSpot instance, so the 0-20
"contact quality" dimension scores how identifiable the human is, not how
senior they are. It is the weakest dimension here and the first one Apollo
should replace with a real title.
"""

# Industry fit — 0 to 40.
INDUSTRY_TIERS: dict[str, int] = {
    "Paper & Forest Products": 40,
    "Libraries": 32,
    "Higher Education": 32,
    "Hospital & Health Care": 26,
    "Telecommunications": 26,
    "Government & Public Sector": 26,
}
INDUSTRY_UNKNOWN = 10

# Geography — 0 to 25.
CORE_STATES = {"OR", "WA"}
SECONDARY_STATES = {"CA", "ID"}

# Contacts we never brief, regardless of firmographics.
ROLE_LOCALPARTS = {
    "accountrep", "accounts", "accounting", "admin", "billing", "contact",
    "hello", "help", "info", "invoices", "mail", "marketing", "noreply",
    "no-reply", "office", "receivables", "sales", "success", "support",
}
PERSONAL_DOMAINS = {
    "aol.com", "gmail.com", "hotmail.com", "icloud.com", "live.com",
    "me.com", "msn.com", "outlook.com", "yahoo.com",
}
# zö's own suppliers and tooling — they are in the CRM as counterparties,
# not as prospects. Grow this list as the team spots more.
VENDOR_DOMAINS = {"e2m.solutions", "simpli.fi"}

_HEX_LOCALPART = re.compile(r"^[0-9a-f]{16,}$")
_TRACKER_DOMAIN = re.compile(r"(^|\.)replies?\.", re.IGNORECASE)


@dataclass
class Lead:
    contact: dict[str, Any]
    company: dict[str, Any] | None
    score: int = 0
    band: str = "Disqualified"
    disqualified_reason: str | None = None
    breakdown: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def load_dataset(path: Path | None = None) -> dict[str, Any]:
    with open(path or DATA_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def disqualify(contact: dict[str, Any]) -> str | None:
    """Return a reason to skip this contact, or None to keep it.

    This is the gate the 1,137-row contact list badly needs: role inboxes,
    machine-generated rows, personal addresses, and zö's own vendors.
    """
    email = (contact.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return "no usable email"
    localpart, domain = email.split("@", 1)
    if _TRACKER_DOMAIN.search(domain):
        return "email-tracker address, not a person"
    if _HEX_LOCALPART.match(localpart):
        return "machine-generated contact row"
    if localpart in ROLE_LOCALPARTS:
        return f"role inbox ({localpart}@), not an individual"
    if domain in PERSONAL_DOMAINS:
        return "personal email domain, no company context"
    if domain in VENDOR_DOMAINS:
        return "zö vendor/supplier, not a prospect"
    return None


def score_industry(company: dict[str, Any] | None) -> tuple[int, str]:
    industry = (company or {}).get("industry")
    if not industry:
        return INDUSTRY_UNKNOWN, "industry unknown — no company match"
    points = INDUSTRY_TIERS.get(industry, INDUSTRY_UNKNOWN)
    if points == INDUSTRY_UNKNOWN:
        return points, f"{industry} is outside zö's observed sector mix"
    return points, f"{industry} is a core zö sector"


def score_geography(company: dict[str, Any] | None) -> tuple[int, str]:
    state = (company or {}).get("state")
    if not state:
        return 5, "location unknown"
    if state in CORE_STATES:
        return 25, f"{state} — Pacific Northwest core territory"
    if state in SECONDARY_STATES:
        return 18, f"{state} — secondary territory"
    return 8, f"{state} — outside observed territory"


def score_contact_quality(contact: dict[str, Any]) -> tuple[int, str]:
    """No title field exists in this instance, so score identifiability."""
    has_name = bool((contact.get("name") or "").strip())
    has_phone = bool((contact.get("phone") or "").strip())
    if has_name and has_phone:
        return 20, "named contact with a direct phone number"
    if has_name:
        return 14, "named contact, no direct phone"
    return 8, "email-only contact — no name on the record"


def score_recency(contact: dict[str, Any], today: date) -> tuple[int, str]:
    raw = contact.get("last_activity")
    if not raw:
        return 0, "no recorded activity"
    days = (today - date.fromisoformat(raw)).days
    if days <= 3:
        return 15, f"active in the last {max(days, 0)} day(s)"
    if days <= 14:
        return 11, f"active {days} days ago"
    if days <= 30:
        return 7, f"active {days} days ago"
    if days <= 90:
        return 3, f"last active {days} days ago — going cold"
    return 0, f"last active {days} days ago — dormant"


def band_for(score: int) -> str:
    if score >= 70:
        return "Hot"
    if score >= 50:
        return "Warm"
    return "Cool"


def build_leads(
    dataset: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[Lead]:
    """Join contacts to companies on email domain, gate, score, sort."""
    data = dataset if dataset is not None else load_dataset()
    today = today or datetime.now(timezone.utc).date()
    by_domain = {c["domain"].lower(): c for c in data.get("companies", [])}

    leads: list[Lead] = []
    for contact in data.get("contacts", []):
        company = by_domain.get(email_domain(contact.get("email") or ""))
        reason = disqualify(contact)
        if reason:
            leads.append(Lead(contact=contact, company=company, disqualified_reason=reason))
            continue

        parts = {
            "industry_fit": score_industry(company),
            "geography": score_geography(company),
            "contact_quality": score_contact_quality(contact),
            "engagement_recency": score_recency(contact, today),
        }
        total = sum(points for points, _ in parts.values())
        leads.append(
            Lead(
                contact=contact,
                company=company,
                score=total,
                band=band_for(total),
                breakdown={key: points for key, (points, _) in parts.items()},
                reasons=[note for _, note in parts.values()],
            )
        )

    leads.sort(key=lambda lead: (lead.disqualified_reason is not None, -lead.score))
    return leads


def build_brief(lead: Lead, case_studies: dict[str, list[str]]) -> dict[str, Any]:
    """Phase 7 outreach prep brief.

    Deliberately produces no messaging. Per the Wave 3 charter the system
    stops before outreach — a human decides whether to reach out and how.
    """
    contact, company = lead.contact, lead.company or {}
    industry = company.get("industry")
    location = ", ".join(p for p in (company.get("city"), company.get("state")) if p)
    return {
        "contact_id": contact["id"],
        "who": contact.get("name") or contact["email"],
        "email": contact["email"],
        "phone": contact.get("phone"),
        "owner": contact.get("owner"),
        "company": company.get("name"),
        "industry": industry,
        "location": location or None,
        "company_data_source": company.get("source"),
        "score": lead.score,
        "band": lead.band,
        "score_breakdown": lead.breakdown,
        "why": lead.reasons,
        "case_studies": case_studies.get(industry or "", []),
        # ponytail: visitor intel is stubbed — RB2B (phase 4) was deferred.
        "visitor_intel": None,
        "next_step": "Human decides whether to reach out, how, and what to say. "
                     "This system drafts no messaging.",
    }


def briefs_for(leads: Iterable[Lead], data: dict[str, Any]) -> list[dict[str, Any]]:
    studies = data.get("case_studies", {})
    return [build_brief(lead, studies) for lead in leads if not lead.disqualified_reason]
