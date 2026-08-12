from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.rfp import RfpRecord
from app.services.justwin_sync.api import JustWinLead


def parse_justwin_date(raw: str) -> str:
    trimmed = (raw or "").strip()
    today = datetime.now(timezone.utc).date().isoformat()
    if not trimmed:
        return today
    try:
        with_year = datetime.strptime(
            f"{trimmed} {datetime.now(timezone.utc).year}", "%b %d %Y"
        )
        return with_year.date().isoformat()
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        pass
    try:
        parsed = datetime.strptime(trimmed, "%Y-%m-%d")
        return parsed.date().isoformat()
    except ValueError:
        return today


# Back-compat for callers that imported the private name.
_parse_justwin_date = parse_justwin_date


def _extract_client(title: str) -> str:
    for_match = re.search(r"\bfor\s+(.+?)(?:\s*\[[A-Z]{2}\])?$", title, re.I)
    if for_match and for_match.group(1):
        return for_match.group(1).strip()
    return re.sub(r"\s*\[[A-Z]{2}\]\s*$", "", title).strip()


def map_lead_to_rfp(lead: JustWinLead, pdf_path: str | None = None) -> RfpRecord:
    now = datetime.now(timezone.utc).isoformat()
    rfp_id = f"rfp-jw-{lead.external_id}"
    title = re.sub(r"\s*\[[A-Z]{2}\]\s*$", "", lead.title).strip()
    return RfpRecord(
        id=rfp_id,
        externalId=lead.external_id,
        title=title,
        client=_extract_client(lead.title),
        source="justwin",
        sector="Public Sector",
        location=lead.location,
        dueDate=_parse_justwin_date(lead.due_date),
        receivedDate=_parse_justwin_date(lead.posted_date),
        stage="intake",
        status="new",
        priority="high" if lead.score >= 4 else "medium",
        fitScore=None,
        worthScore=None,
        goNoGo=None,
        assignedTo=None,
        estimatedValue=None,
        lastActivity=now,
        lastActivityNote=f"Synced from JustWin ({lead.tab} leads)",
        contractRole="prime",
        description=lead.description,
        justwinTab=lead.tab,
        pdfPath=pdf_path,
        justwinDetailUrl=lead.detail_url,
        syncedAt=now,
        pdfUrl=f"/api/rfps/{rfp_id}/pdf" if pdf_path else None,
    )
