"""Read-only HubSpot mirror for the Wave 3 Lead Finder.

HubSpot is the source of truth for people. This module only ever GETs (or
POSTs to search/read endpoints) — it never writes to HubSpot. Contacts and
companies are copied into hs_contacts / hs_companies, and the Lead Finder
reads the copy, so page loads never spend HubSpot rate limit.

Sync modes:
  full  — page through every contact and company; archive/delete rows HubSpot no longer has.
  auto  — search for records modified since the saved watermark; falls back to full
          on the first run or when the change set exceeds HubSpot's 10k search cap.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
SEARCH_CAP = 10_000  # HubSpot search never returns more than 10k rows for one query
# Search indexing lags writes by a few seconds; re-read a small overlap. Upserts are idempotent.
WATERMARK_OVERLAP = timedelta(minutes=5)
_UPSERT_BATCH = 500

CONTACT_PROPS = [
    "email", "firstname", "lastname", "jobtitle", "phone", "company", "website",
    "city", "state", "country", "industry", "hubspot_owner_id", "associatedcompanyid",
    "lifecyclestage", "hs_lead_status", "lastmodifieddate",
    "notes_last_contacted", "notes_last_updated", "num_contacted_notes",
    "hs_last_sales_activity_timestamp",
    "hs_analytics_num_visits", "hs_analytics_num_page_views",
    "hs_analytics_last_visit_timestamp", "hs_analytics_first_url",
    "hs_analytics_last_url", "hs_analytics_source",
    "role", "contact_status",  # zö's own fields, see contact_exclusion
]
COMPANY_PROPS = [
    "domain", "name", "industry", "city", "state", "country",
    "numberofemployees", "hs_lastmodifieddate",
    "type", "relationship_type", "status",  # see company_exclusion
]
DEAL_PROPS = [
    "dealname", "amount", "dealstage", "pipeline", "closedate",
    "hs_is_closed", "hs_is_closed_won", "hs_is_closed_lost",
    "hs_deal_stage_probability", "hubspot_owner_id", "hs_lastmodifieddate",
]
# object type -> (properties, modified-date property used for incremental search)
OBJECTS: dict[str, tuple[list[str], str]] = {
    "companies": (COMPANY_PROPS, "hs_lastmodifieddate"),
    "contacts": (CONTACT_PROPS, "lastmodifieddate"),
    "deals": (DEAL_PROPS, "hs_lastmodifieddate"),
}

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


class HubSpotError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.hubspot_api_key.strip())


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {settings.hubspot_api_key.strip()}"},
        timeout=30.0,
    )


def _call(http: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """One HubSpot request, retrying 429s and 5xx with HubSpot's Retry-After when given."""
    for attempt in range(4):
        response = http.request(method, path, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            delay = float(response.headers.get("Retry-After") or 2 ** attempt)
            logger.warning(
                "operation=hubspot_call path=%s status=%s retry_in_s=%s",
                path, response.status_code, delay,
            )
            time.sleep(delay)
            continue
        if response.status_code >= 400:
            raise HubSpotError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()
    raise HubSpotError(f"{method} {path} still failing after retries")


def _next_after(page: dict[str, Any]) -> str | None:
    return ((page.get("paging") or {}).get("next") or {}).get("after")


def iter_all(http: httpx.Client, object_type: str) -> Iterator[dict[str, Any]]:
    props, _ = OBJECTS[object_type]
    params: dict[str, Any] = {"limit": 100, "properties": ",".join(props), "archived": "false"}
    if object_type == "deals":
        params["associations"] = "companies"
    while True:
        page = _call(http, "GET", f"/crm/v3/objects/{object_type}", params=params)
        yield from page.get("results") or []
        after = _next_after(page)
        if not after:
            return
        params["after"] = after


def search_since(http: httpx.Client, object_type: str, since: datetime) -> list[dict[str, Any]] | None:
    """Records modified at or after `since`, or None when there are too many to search."""
    props, date_prop = OBJECTS[object_type]
    body: dict[str, Any] = {
        "filterGroups": [{"filters": [{
            "propertyName": date_prop,
            "operator": "GTE",
            "value": str(int(since.timestamp() * 1000)),
        }]}],
        "sorts": [{"propertyName": date_prop, "direction": "ASCENDING"}],
        "properties": props,
        "limit": 200,
    }
    # Search API does not return associations; deal company ids are filled on full sync
    # or left null until the next full nightly. Incremental still gets amounts/stages.
    rows: list[dict[str, Any]] = []
    while True:
        page = _call(http, "POST", f"/crm/v3/objects/{object_type}/search", json=body)
        if int(page.get("total") or 0) >= SEARCH_CAP:
            return None
        rows.extend(page.get("results") or [])
        after = _next_after(page)
        if not after:
            return rows
        body["after"] = after


def owner_names(http: httpx.Client) -> dict[str, str]:
    """owner id -> display name. Missing scope degrades to no owner names, not a failed sync."""
    names: dict[str, str] = {}
    params: dict[str, Any] = {"limit": 100}
    try:
        while True:
            page = _call(http, "GET", "/crm/v3/owners", params=params)
            for owner in page.get("results") or []:
                name = " ".join(p for p in (owner.get("firstName"), owner.get("lastName")) if p)
                names[str(owner.get("id"))] = name or owner.get("email") or ""
            after = _next_after(page)
            if not after:
                return names
            params["after"] = after
    except HubSpotError as exc:
        logger.warning("operation=hubspot_owner_names status=failed error=%s", exc)
        return names


def industry_labels(http: httpx.Client) -> dict[str, str]:
    """HubSpot stores industry as an enum (PAPER_FOREST_PRODUCTS); scoring wants the UI label."""
    try:
        prop = _call(http, "GET", "/crm/v3/properties/companies/industry")
    except HubSpotError as exc:
        logger.warning("operation=hubspot_industry_labels status=failed error=%s", exc)
        return {}
    return {o["value"]: o["label"] for o in prop.get("options") or [] if o.get("value")}


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _hs_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes")


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _associated_company_id(obj: dict[str, Any]) -> int | None:
    associations = obj.get("associations") or {}
    companies = associations.get("companies") or {}
    results = companies.get("results") or []
    if not results:
        return None
    return _int(results[0].get("id"))


def normalize_state(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    if len(text) == 2:
        return text.upper()
    return US_STATES.get(text.lower(), text)


def contact_row(obj: dict[str, Any], owners: dict[str, str], synced_at: str) -> dict[str, Any]:
    p = obj.get("properties") or {}
    email = _text(p.get("email"))
    return {
        "hs_id": int(obj["id"]),
        "email": email.lower() if email else None,
        "firstname": _text(p.get("firstname")),
        "lastname": _text(p.get("lastname")),
        "jobtitle": _text(p.get("jobtitle")),
        "phone": _text(p.get("phone")),
        "company_hs_id": _int(p.get("associatedcompanyid")),
        "owner_name": owners.get(str(p.get("hubspot_owner_id") or "")) or None,
        "last_activity_at": _text(p.get("notes_last_updated")),
        "properties": p,
        "hs_updated_at": obj.get("updatedAt") or p.get("lastmodifieddate"),
        "archived": bool(obj.get("archived")),
        "synced_at": synced_at,
    }


def company_row(obj: dict[str, Any], labels: dict[str, str], synced_at: str) -> dict[str, Any]:
    p = obj.get("properties") or {}
    domain = _text(p.get("domain"))
    industry = _text(p.get("industry"))
    return {
        "hs_id": int(obj["id"]),
        "domain": domain.lower() if domain else None,
        "name": _text(p.get("name")),
        "industry": labels.get(industry, industry) if industry else None,
        "city": _text(p.get("city")),
        "state": _text(p.get("state")),
        "properties": p,
        "hs_updated_at": obj.get("updatedAt") or p.get("hs_lastmodifieddate"),
        "synced_at": synced_at,
    }


def deal_row(obj: dict[str, Any], owners: dict[str, str], synced_at: str) -> dict[str, Any]:
    p = obj.get("properties") or {}
    return {
        "hs_id": int(obj["id"]),
        "dealname": _text(p.get("dealname")),
        "amount": _float(p.get("amount")),
        "dealstage": _text(p.get("dealstage")),
        "pipeline": _text(p.get("pipeline")),
        "closedate": _text(p.get("closedate")),
        "hs_is_closed": _hs_bool(p.get("hs_is_closed")),
        "hs_is_closed_won": _hs_bool(p.get("hs_is_closed_won")),
        "hs_is_closed_lost": _hs_bool(p.get("hs_is_closed_lost")),
        "stage_probability": _float(p.get("hs_deal_stage_probability")),
        "company_hs_id": _associated_company_id(obj),
        "owner_name": owners.get(str(p.get("hubspot_owner_id") or "")) or None,
        "properties": p,
        "hs_updated_at": obj.get("updatedAt") or p.get("hs_lastmodifieddate"),
        "archived": bool(obj.get("archived")),
        "synced_at": synced_at,
    }


def stage_rows(pipeline: dict[str, Any], synced_at: str) -> list[dict[str, Any]]:
    pipe_id = str(pipeline.get("id") or "")
    pipe_label = _text(pipeline.get("label"))
    rows: list[dict[str, Any]] = []
    for stage in pipeline.get("stages") or []:
        meta = stage.get("metadata") or {}
        rows.append({
            "pipeline_id": pipe_id,
            "stage_id": str(stage.get("id") or ""),
            "pipeline_label": pipe_label,
            "stage_label": _text(stage.get("label")),
            "probability": _float(meta.get("probability")),
            "is_closed": _hs_bool(meta.get("isClosed")),
            "display_order": _int(stage.get("displayOrder")),
            "synced_at": synced_at,
        })
    return rows


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- Supabase ---------------------------------------------------------------

def _db():
    from app.services.supabase_db import _get_client

    return _get_client()


def _upsert(db: Any, table: str, rows: list[dict[str, Any]], *, on_conflict: str = "hs_id") -> None:
    for start in range(0, len(rows), _UPSERT_BATCH):
        db.table(table).upsert(
            rows[start : start + _UPSERT_BATCH], on_conflict=on_conflict
        ).execute()


def sync_deal_stages(http: httpx.Client, db: Any, synced_at: str) -> int:
    """Refresh pipeline stage catalog (labels + probabilities)."""
    page = _call(http, "GET", "/crm/v3/pipelines/deals")
    rows: list[dict[str, Any]] = []
    for pipe in page.get("results") or []:
        rows.extend(stage_rows(pipe, synced_at))
    if rows:
        _upsert(db, "hs_deal_stages", rows, on_conflict="pipeline_id,stage_id")
    logger.info("operation=hubspot_sync_deal_stages count=%s", len(rows))
    return len(rows)


def _select_all(db: Any, table: str, columns: str, **eq: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1000
    while True:
        query = db.table(table).select(columns).order("hs_id")
        for key, value in eq.items():
            query = query.eq(key, value)
        chunk = query.range(len(rows), len(rows) + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows


def _watermarks(db: Any) -> dict[str, datetime | None]:
    rows = db.table("hubspot_sync_state").select("object_type,watermark").execute().data or []
    return {r["object_type"]: _parse_ts(r.get("watermark")) for r in rows}


def run_sync(mode: str = "auto") -> dict[str, Any]:
    """Pull HubSpot into the mirror. Read-only against HubSpot.

    ponytail: no lease. The 15-min and nightly jobs can overlap; every write is an
    idempotent upsert and watermarks only move forward, so overlap costs API calls,
    not correctness. Add a lease like qb_sync_state if runs start taking minutes.
    """
    if not configured():
        raise HubSpotError("HUBSPOT_API_KEY is not set")
    db = _db()
    started = datetime.now(timezone.utc)
    synced_at = started.isoformat()
    run = db.table("hubspot_sync_runs").insert(
        {"mode": mode, "started_at": synced_at, "status": "running"}
    ).execute().data
    run_id = run[0]["id"] if run else None
    counts: dict[str, Any] = {}
    try:
        marks = _watermarks(db)
        with _client() as http:
            owners = owner_names(http)
            labels = industry_labels(http)
            sync_deal_stages(http, db, synced_at)
            for object_type in ("companies", "contacts", "deals"):
                mark = marks.get(object_type)
                raw = None
                if mode != "full" and mark:
                    raw = search_since(http, object_type, mark - WATERMARK_OVERLAP)
                full = raw is None
                if full:
                    raw = list(iter_all(http, object_type))
                if object_type == "contacts":
                    rows = [contact_row(o, owners, synced_at) for o in raw]
                elif object_type == "deals":
                    rows = [deal_row(o, owners, synced_at) for o in raw]
                else:
                    rows = [company_row(o, labels, synced_at) for o in raw]
                _upsert(db, f"hs_{object_type}", rows)
                counts[f"{object_type}_upserted"] = len(rows)
                counts[f"{object_type}_mode"] = "full" if full else "incremental"

                if full:
                    counts[f"{object_type}_removed"] = _drop_stale(db, object_type, synced_at)
                newest = max((_parse_ts(r["hs_updated_at"]) for r in rows if r.get("hs_updated_at")), default=None)
                if newest and (mark is None or newest > mark):
                    db.table("hubspot_sync_state").upsert(
                        {"object_type": object_type, "watermark": newest.isoformat()},
                        on_conflict="object_type",
                    ).execute()
    except Exception as exc:
        if run_id is not None:
            db.table("hubspot_sync_runs").update({
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)[:500],
            }).eq("id", run_id).execute()
        logger.error("operation=hubspot_sync mode=%s status=failed error=%s", mode, exc)
        raise
    if run_id is not None:
        db.table("hubspot_sync_runs").update({
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "contacts_upserted": counts.get("contacts_upserted"),
            "companies_upserted": counts.get("companies_upserted"),
            "contacts_archived": counts.get("contacts_removed"),
            "deals_upserted": counts.get("deals_upserted"),
            "deals_archived": counts.get("deals_removed"),
        }).eq("id", run_id).execute()
    logger.info("operation=hubspot_sync mode=%s status=completed %s", mode, counts)
    return {"run_id": run_id, "mode": mode, **counts}


def _drop_stale(db: Any, object_type: str, synced_at: str) -> int:
    """After a full pass, anything not touched this run is gone from HubSpot.

    Contacts and deals are archived (downstream may still reference them); companies
    are deleted so a removed company stops matching in the domain join.
    """
    if object_type in ("contacts", "deals"):
        gone = (
            db.table(f"hs_{object_type}").update({"archived": True})
            .lt("synced_at", synced_at).eq("archived", False).execute().data
        )
    else:
        gone = db.table("hs_companies").delete().lt("synced_at", synced_at).execute().data
    return len(gone or [])


# --- Lead Finder dataset ----------------------------------------------------

# --- "Not a prospect" flags the team sets in HubSpot -------------------------
# These scale where a hard-coded list cannot: when someone marks a company as a
# Vendor in HubSpot, every contact at it drops out of the queue on the next sync.
# Company Type "Other" is deliberately ignored: zö uses it for agencies but also
# for real prospects (GEICO, a clinic), so it cannot mean "exclude".

def company_exclusion(company: dict[str, Any]) -> str | None:
    rel = company.get("relationship_type")
    if company.get("status") == "Do Not Contact" or rel == "Do Not Contact":
        return "HubSpot: company marked Do Not Contact"
    if company.get("type") == "VENDOR" or rel == "Vendor":
        return "HubSpot: company marked Vendor"
    if rel in ("Media", "Agency Network"):
        return f"HubSpot: company marked {rel}"
    return None


def contact_exclusion(contact: dict[str, Any]) -> str | None:
    status, role = contact.get("contact_status"), contact.get("role")
    if status in ("Do Not Contact", "Former"):
        return f"HubSpot: contact marked {status}"
    if role in ("Vendor/partner", "Agency Network"):
        return f"HubSpot: contact role {role}"
    return None


def _excluded_domains(contacts, companies_by_id) -> dict[str, str]:
    """Email domains that belong to a flagged company, so colleagues who were never
    linked to the company record in HubSpot are caught too. Personal domains never
    count: one vendor rep on gmail must not flag every gmail contact."""
    from app.leads.scoring import PERSONAL_DOMAINS, email_domain

    found: dict[str, str] = {}
    unlinked: list[tuple[str, str]] = []  # (name slug, reason) for flagged companies with no domain
    for c in companies_by_id.values():
        reason = company_exclusion(c)
        if reason and c.get("domain"):
            found.setdefault(c["domain"].lower(), reason)
        elif reason and c.get("name"):
            unlinked.append((re.sub(r"[^a-z0-9]", "", c["name"].lower()), reason))
    # Most flagged companies in zö's HubSpot have no domain, so match their name to
    # contact email domains: "Kopp Consulting" -> koppconsultingusa.com.
    # ponytail: name heuristic. Short names (VFS, Koze) must match the domain exactly;
    # longer ones may prefix it. Filling in the company's domain in HubSpot beats this.
    for c in contacts:
        domain = email_domain(c.get("email") or "")
        root = domain.split(".")[0]
        for slug, reason in unlinked:
            if slug and (root == slug or (len(slug) >= 6 and root.startswith(slug))):
                found.setdefault(domain, reason)
                break
    for c in contacts:
        company = companies_by_id.get(c.get("company_hs_id"))
        reason = (company and company_exclusion(company)) or (
            contact_exclusion(c) if c.get("role") else None  # a role describes the company; a status does not
        )
        domain = email_domain(c.get("email") or "")
        if reason and domain:
            found.setdefault(domain, reason)
    return {d: r for d, r in found.items() if d not in PERSONAL_DOMAINS}


def dataset_from_rows(contacts: list[dict[str, Any]], companies: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape mirror rows like data/leads_poc.json so scoring.build_leads needs no changes."""
    from app.leads.scoring import email_domain

    companies_by_id = {c["hs_id"]: c for c in companies}
    flagged_domains = _excluded_domains(contacts, companies_by_id)

    def excluded(c: dict[str, Any]) -> str | None:
        company = companies_by_id.get(c.get("company_hs_id"))
        return (
            contact_exclusion(c)
            or (company_exclusion(company) if company else None)
            or flagged_domains.get(email_domain(c.get("email") or ""))
        )

    return {
        "source": "hubspot",
        "companies": [
            {
                "id": str(c["hs_id"]),
                "domain": c.get("domain") or "",
                "name": c.get("name"),
                "industry": c.get("industry"),
                "city": c.get("city"),
                "state": normalize_state(c.get("state")),
                "source": "hubspot",
            }
            for c in companies
        ],
        "contacts": [
            {
                "id": str(c["hs_id"]),
                "name": " ".join(p for p in (c.get("firstname"), c.get("lastname")) if p) or None,
                "email": c.get("email") or "",
                "phone": c.get("phone"),
                "owner": c.get("owner_name"),
                "last_activity": (c.get("last_activity_at") or "")[:10] or None,
                "company_id": str(c["company_hs_id"]) if c.get("company_hs_id") else None,
                "excluded_reason": excluded(c),
            }
            for c in contacts
        ],
    }


def load_mirror_dataset() -> dict[str, Any] | None:
    """The mirror as a Lead Finder dataset, or None until HubSpot is configured and synced."""
    if not configured():
        return None
    db = _db()
    contacts = _select_all(
        db, "hs_contacts",
        "hs_id,email,firstname,lastname,phone,company_hs_id,owner_name,last_activity_at,"
        "role:properties->>role,contact_status:properties->>contact_status",
        archived=False,
    )
    if not contacts:
        return None
    companies = _select_all(
        db, "hs_companies",
        "hs_id,domain,name,industry,city,state,type:properties->>type,"
        "relationship_type:properties->>relationship_type,status:properties->>status",
    )
    last = (
        db.table("hubspot_sync_runs").select("finished_at").eq("status", "completed")
        .order("finished_at", desc=True).limit(1).execute().data
    )
    return {**dataset_from_rows(contacts, companies), "synced_at": last[0]["finished_at"] if last else None}
