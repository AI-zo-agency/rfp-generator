from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from playwright.sync_api import Page

from app.core.config import settings
from app.services.justwin_sync.browser import get_justwin_base_url

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


def _api_root() -> str:
    return (settings.justwin_api_root or "https://api.justwin.ai").rstrip("/")


LifecycleState = Literal["hot", "warm", "review"]
LIFECYCLE_STATES: tuple[LifecycleState, ...] = ("hot", "warm", "review")


@dataclass
class JustWinLead:
    external_id: str
    title: str
    location: str
    posted_date: str
    due_date: str
    score: int
    description: str
    detail_url: str
    tab: LifecycleState


@dataclass
class JustWinApiClient:
    page: Page
    headers: dict[str, str]
    company_id: str


def posted_date_of(lead: dict[str, Any]) -> str:
    """Primary posted date for storage — UTC calendar date of ``created``."""
    created = _parse_created(lead)
    if created is None:
        return ""
    return created.astimezone(timezone.utc).date().isoformat()


def _parse_created(lead: dict[str, Any]) -> datetime | None:
    created = lead.get("created")
    if not created:
        return None
    try:
        raw = str(created).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


# JustWin's "Posted" column is rendered in the viewer's local timezone (and many
# US users see Eastern/Pacific). Sync "Today" used to filter on UTC-only, so a
# lead created 2026-08-06T20:00Z showed as Aug 7 in IST/US evening but was
# skipped when syncing 2026-08-07 → 0 leads found.
_POSTED_MATCH_ZONES: tuple[str, ...] = (
    "UTC",
    "America/New_York",
    "America/Los_Angeles",
    "America/Chicago",
    "Asia/Kolkata",
)


def _calendar_dates_for_created(created: datetime) -> set[str]:
    dates: set[str] = set()
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover
        dates.add(created.astimezone(timezone.utc).date().isoformat())
        return dates
    for name in _POSTED_MATCH_ZONES:
        try:
            dates.add(created.astimezone(ZoneInfo(name)).date().isoformat())
        except Exception:  # noqa: BLE001 — unknown tz on older hosts
            continue
    if not dates:
        dates.add(created.astimezone(timezone.utc).date().isoformat())
    return dates


def lead_matches_posted_date(lead: dict[str, Any], target_date: str) -> bool:
    """True when JustWin would show this lead as posted on ``target_date``."""
    target = (target_date or "").strip()
    if not target:
        return True
    created = _parse_created(lead)
    if created is None:
        return False
    return target in _calendar_dates_for_created(created)


def _lead_is_older_than_target(lead: dict[str, Any], target_date: str) -> bool:
    """Safe pagination stop: every display-zone calendar date is before target."""
    created = _parse_created(lead)
    if created is None:
        return False
    dates = _calendar_dates_for_created(created)
    return bool(dates) and max(dates) < target_date


def create_api_client(page: Page) -> JustWinApiClient:
    token = page.evaluate("() => localStorage.getItem('token')")
    if not token:
        raise RuntimeError(
            "JustWin auth token not found — delete session file and rerun sync"
        )
    headers = {"Authorization": f"Bearer {token}"}

    companies_res = page.request.get(f"{_api_root()}/companies", headers=headers)
    if not companies_res.ok:
        raise RuntimeError(f"JustWin companies API failed ({companies_res.status})")
    companies = companies_res.json()
    results = companies.get("results") or []
    if not results or not results[0].get("id"):
        raise RuntimeError("JustWin companies API returned no company")
    company_id = str(results[0]["id"])
    return JustWinApiClient(page=page, headers=headers, company_id=company_id)


def _to_lead(raw: dict[str, Any], tab: LifecycleState) -> JustWinLead:
    readonly = raw.get("readonly_values") or {}
    insights = readonly.get("insights") or {}
    title = readonly.get("name") or insights.get("title") or "Untitled solicitation"
    state = raw.get("state") or {}
    return JustWinLead(
        external_id=str(raw["id"]),
        title=str(title),
        location=str(state.get("abbreviation") or ""),
        posted_date=posted_date_of(raw),
        due_date=str(raw.get("due_date") or ""),
        score=int(readonly.get("relevance_score_integer") or 0),
        description=str(insights.get("summary") or title),
        detail_url=f"{get_justwin_base_url()}/leads/{raw['id']}/summary",
        tab=tab,
    )


def fetch_leads_for_tab(
    client: JustWinApiClient,
    tab: LifecycleState,
    target_date: str | None = None,
) -> list[JustWinLead]:
    leads: list[JustWinLead] = []
    url = (
        f"{_api_root()}/leads?company={client.company_id}&assigned=true"
        f"&page_size={PAGE_SIZE}&ordering=-created&jurisdiction=all"
        f"&lifecycle_state={tab}&page=1"
    )
    pages = 0

    while url:
        res = client.page.request.get(url, headers=client.headers)
        if not res.ok:
            raise RuntimeError(f'JustWin leads API failed for "{tab}" ({res.status})')
        body = res.json()
        pages += 1
        older_than_target = False
        page_results = body.get("results") or []
        if target_date and pages == 1 and page_results:
            samples: list[str] = []
            for raw in page_results[:5]:
                created = _parse_created(raw)
                if created is None:
                    samples.append("no-created")
                    continue
                zones = ",".join(sorted(_calendar_dates_for_created(created)))
                samples.append(f"utc={posted_date_of(raw)} zones={zones}")
            logger.info(
                "[justwin-sync] %s first-page count=%s created dates: %s",
                tab,
                len(page_results),
                samples,
            )
        for raw in page_results:
            if target_date:
                if _lead_is_older_than_target(raw, target_date):
                    older_than_target = True
                    continue
                if not lead_matches_posted_date(raw, target_date):
                    continue
            leads.append(_to_lead(raw, tab))

        if target_date and older_than_target:
            break
        url = body.get("next") or ""

    logger.info(
        "[justwin-sync] %s: %s lead(s)%s (%s page(s) scanned)",
        tab,
        len(leads),
        f" posted {target_date}" if target_date else "",
        pages,
    )
    return leads


def resolve_pdf_url(client: JustWinApiClient, lead_id: str) -> str | None:
    lead_res = client.page.request.get(
        f"{_api_root()}/leads/{lead_id}", headers=client.headers
    )
    if not lead_res.ok:
        return None
    lead = lead_res.json()
    if lead.get("documentless") or not lead.get("target"):
        return None
    view_res = client.page.request.get(
        f"{_api_root()}/targets/{lead['target']}/view",
        headers=client.headers,
    )
    if not view_res.ok:
        return None
    return (view_res.json() or {}).get("url")


def download_solicitation_pdf_bytes(
    client: JustWinApiClient, external_id: str
) -> bytes | None:
    s3_url = resolve_pdf_url(client, external_id)
    if not s3_url:
        logger.info("[justwin-sync] %s: no solicitation document", external_id)
        return None
    pdf_response = client.page.request.get(s3_url)
    if not pdf_response.ok:
        raise RuntimeError(f"Failed to download PDF from S3 ({pdf_response.status})")
    body = pdf_response.body()
    if len(body) < 500 or not body.startswith(b"%PDF"):
        raise RuntimeError("Downloaded file was not a valid PDF")
    return body


def resolve_tabs(target_tab: str) -> list[LifecycleState]:
    requested = (target_tab or "all").lower()
    if requested == "all":
        return list(LIFECYCLE_STATES)
    if requested in LIFECYCLE_STATES:
        return [requested]  # type: ignore[list-item]
    raise ValueError(
        f'Unknown JustWin tab "{target_tab}". Expected one of: all, '
        + ", ".join(LIFECYCLE_STATES)
    )


def collect_leads(
    client: JustWinApiClient,
    target_date: str | None = None,
    target_tab: str = "all",
) -> list[JustWinLead]:
    tabs = resolve_tabs(target_tab)
    date_filter = (target_date or "").strip() or None
    logger.info(
        "[justwin-sync] tab(s): %s, posted date: %s",
        ", ".join(tabs),
        date_filter or "any",
    )
    all_leads: list[JustWinLead] = []
    seen: set[str] = set()
    for tab in tabs:
        for lead in fetch_leads_for_tab(client, tab, date_filter):
            if lead.external_id in seen:
                continue
            seen.add(lead.external_id)
            all_leads.append(lead)
    return all_leads
