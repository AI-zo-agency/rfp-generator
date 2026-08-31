"""Supabase Postgres CRUD for RFP app data."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

import httpx

from app.models.go_no_go import GoNoGoAnalysis
from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services import supabase_storage

logger = logging.getLogger(__name__)

_client_local = threading.local()
_T = TypeVar("_T")
_TRANSIENT = (
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.ProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.TimeoutException,
    OSError,
    ConnectionError,
)


def reset_supabase_client() -> None:
    """Drop this thread's cached client so the next request opens a fresh connection."""
    _client_local.client = None


def _with_transient_retry(op_name: str, fn: Callable[[], _T], *, attempts: int = 3) -> _T:
    """Retry flaky Supabase HTTP disconnects (common under polling + sync load)."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except _TRANSIENT as exc:
            last = exc
            reset_supabase_client()
            if attempt >= attempts - 1:
                break
            delay = 0.2 * (attempt + 1)
            logger.warning(
                "Supabase transient failure in %s (attempt %d/%d): %s",
                op_name,
                attempt + 1,
                attempts,
                exc,
            )
            time.sleep(delay)
    assert last is not None
    raise last


class SupabaseDbError(Exception):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def use_supabase_db() -> bool:
    return supabase_storage.is_configured()


def _get_client():
    cached = getattr(_client_local, "client", None)
    if cached is not None:
        return cached
    if not use_supabase_db():
        raise SupabaseDbError("Supabase is not configured", status_code=503)
    try:
        from supabase import create_client
        from supabase.lib.client_options import SyncClientOptions
    except ImportError as exc:
        raise SupabaseDbError(
            "supabase package not installed — pip install supabase",
            status_code=503,
        ) from exc

    from app.core.config import settings

    # ponytail: one sync httpx.Client per thread; shared clients + HTTP/2 corrupt under
    # FastAPI's thread pool (LocalProtocolError / httpcore KeyError on stream cleanup).
    http_client = httpx.Client(http2=False, timeout=120.0)
    options = SyncClientOptions(httpx_client=http_client, postgrest_client_timeout=120)
    _client_local.client = create_client(
        settings.supabase_url.strip(),
        settings.supabase_service_role_key.strip(),
        options=options,
    )
    return _client_local.client


def _iso(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_analysis(raw: Any) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _dict_to_rfp(row: dict[str, Any]) -> RfpRecord:
    rfp_id = str(row["id"])
    pdf_path = row.get("pdf_path")
    return RfpRecord(
        id=rfp_id,
        externalId=row.get("external_id"),
        title=row.get("title") or "",
        client=row.get("client") or "",
        source=row.get("source") or "justwin",
        sector=row.get("sector") or "Public Sector",
        location=row.get("location") or "",
        dueDate=row.get("due_date") or "",
        receivedDate=row.get("received_date") or "",
        stage=row.get("stage") or "intake",
        status=row.get("status") or "new",
        priority=row.get("priority") or "medium",
        fitScore=row.get("fit_score"),
        worthScore=row.get("worth_score"),
        goNoGo=row.get("go_no_go"),
        assignedTo=row.get("assigned_to"),
        estimatedValue=row.get("estimated_value"),
        pageLimit=row.get("page_limit"),
        lastActivity=_iso(row.get("last_activity")) or "",
        lastActivityNote=row.get("last_activity_note") or "",
        contractRole=row.get("contract_role") or "prime",
        description=row.get("description"),
        justwinTab=row.get("justwin_tab"),
        pdfPath=pdf_path,
        justwinDetailUrl=row.get("justwin_detail_url"),
        syncedAt=_iso(row.get("synced_at")),
        goNoGoAnalysis=_parse_analysis(row.get("go_no_go_analysis")),
        pdfUrl=None,
    )


def _rfp_to_row(record: RfpRecord, *, go_no_go_analysis: Any | None = ...) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": record.id,
        "external_id": record.external_id or record.id,
        "title": record.title,
        "client": record.client,
        "source": record.source,
        "sector": record.sector,
        "location": record.location,
        "due_date": record.due_date,
        "received_date": record.received_date,
        "stage": record.stage,
        "status": record.status,
        "priority": record.priority,
        "fit_score": record.fit_score,
        "worth_score": record.worth_score,
        "go_no_go": record.go_no_go,
        "assigned_to": record.assigned_to,
        "estimated_value": record.estimated_value,
        "page_limit": record.page_limit,
        "last_activity": record.last_activity or None,
        "last_activity_note": record.last_activity_note,
        "contract_role": record.contract_role,
        "description": record.description,
        "justwin_tab": record.justwin_tab,
        "pdf_path": record.pdf_path,
        "justwin_detail_url": record.justwin_detail_url,
        "synced_at": record.synced_at,
    }
    if go_no_go_analysis is not ...:
        row["go_no_go_analysis"] = go_no_go_analysis
    elif record.go_no_go_analysis is not None:
        row["go_no_go_analysis"] = record.go_no_go_analysis
    return row


# Columns owned by Go/No-Go analysis or by the user's workflow rather than by the
# JustWin feed. `upsert_rfp` keeps whatever is already stored for these so a
# re-sync cannot silently undo an analysis or a stage change.
_PRESERVED_ON_RESYNC = (
    "fit_score",
    "worth_score",
    "go_no_go",
    "go_no_go_analysis",
    "stage",
    "status",
    "assigned_to",
    "estimated_value",
    "page_limit",
    "pdf_path",
)


def _dedupe_by_title(rfps: list[RfpRecord]) -> list[RfpRecord]:
    by_title: dict[str, RfpRecord] = {}
    for rfp in rfps:
        key = rfp.title.lower()
        existing = by_title.get(key)
        if not existing:
            by_title[key] = rfp
            continue
        prefer_current = bool(rfp.pdf_path and not existing.pdf_path) or (
            (rfp.synced_at or "") > (existing.synced_at or "")
        )
        if prefer_current:
            by_title[key] = rfp
    return sorted(
        by_title.values(),
        key=lambda item: item.received_date,
        reverse=True,
    )


def _handle_response(data: Any, *, context: str) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise SupabaseDbError(f"Unexpected Supabase response for {context}")


def ping() -> None:
    client = _get_client()
    client.table("rfps").select("id").limit(1).execute()


def list_rfps() -> list[RfpRecord]:
    client = _get_client()
    result = (
        client.table("rfps")
        .select("*")
        .order("synced_at", desc=True)
        .order("received_date", desc=True)
        .execute()
    )
    rows = _handle_response(result.data, context="list_rfps")
    return _dedupe_by_title([_dict_to_rfp(row) for row in rows])


def get_rfp(rfp_id: str) -> RfpRecord | None:
    client = _get_client()
    result = (
        client.table("rfps")
        .select("*")
        .or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}")
        .limit(1)
        .execute()
    )
    rows = _handle_response(result.data, context="get_rfp")
    return _dict_to_rfp(rows[0]) if rows else None


def rfp_exists(rfp_id: str) -> bool:
    client = _get_client()
    result = (
        client.table("rfps")
        .select("id")
        .or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}")
        .limit(1)
        .execute()
    )
    rows = _handle_response(result.data, context="rfp_exists")
    return bool(rows)


def find_existing_justwin_rfp(
    *,
    external_id: str,
    title: str = "",
    received_date: str = "",
) -> RfpRecord | None:
    """Match an already-imported JustWin lead so re-sync skips duplicates."""
    external_id = (external_id or "").strip()
    client = _get_client()
    if external_id:
        result = (
            client.table("rfps")
            .select("*")
            .or_(
                f"external_id.eq.{external_id},"
                f"id.eq.{external_id},"
                f"id.eq.rfp-jw-{external_id}"
            )
            .limit(1)
            .execute()
        )
        rows = _handle_response(result.data, context="find_existing_justwin_rfp id")
        if rows:
            return _dict_to_rfp(rows[0])

    title_key = (title or "").strip().casefold()
    date_key = (received_date or "").strip()[:10]
    if not title_key or not date_key:
        return None
    result = (
        client.table("rfps")
        .select("*")
        .eq("source", "justwin")
        .eq("received_date", date_key)
        .execute()
    )
    rows = _handle_response(result.data, context="find_existing_justwin_rfp title")
    for row in rows:
        if (row.get("title") or "").strip().casefold() == title_key:
            return _dict_to_rfp(row)
    return None


def get_rfp_pdf_path(rfp_id: str) -> str | None:
    client = _get_client()
    result = (
        client.table("rfps")
        .select("pdf_path")
        .or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}")
        .limit(1)
        .execute()
    )
    rows = _handle_response(result.data, context="get_rfp_pdf_path")
    if not rows:
        return None
    path = rows[0].get("pdf_path")
    return path if path else None


def insert_rfp(record: RfpRecord) -> RfpRecord:
    client = _get_client()
    client.table("rfps").insert(_rfp_to_row(record)).execute()
    return record


def upsert_rfp(record: RfpRecord) -> None:
    """Upsert a synced solicitation by external_id.

    A sync refreshes what JustWin knows (title, dates, description, document) but
    must never reset what we decided: Go/No-Go results, workflow stage, and
    assignment all survive a re-sync.
    """
    client = _get_client()
    external_id = record.external_id or record.id
    existing_row: dict[str, Any] = {}
    try:
        existing = (
            client.table("rfps")
            .select(",".join(_PRESERVED_ON_RESYNC))
            .eq("external_id", external_id)
            .limit(1)
            .execute()
        )
        rows = _handle_response(existing.data, context="upsert_rfp lookup")
        if rows:
            existing_row = rows[0] or {}
    except Exception:
        logger.debug("No existing row for external_id=%s", external_id)

    row = _rfp_to_row(record)
    for column in _PRESERVED_ON_RESYNC:
        existing_value = existing_row.get(column)
        if existing_value is not None and existing_value != "":
            row[column] = existing_value

    client.table("rfps").upsert(row, on_conflict="external_id").execute()


def update_rfp_pdf_path(rfp_id: str, pdf_path: str) -> None:
    client = _get_client()
    client.table("rfps").update({"pdf_path": pdf_path}).or_(
        f"id.eq.{rfp_id},external_id.eq.{rfp_id}"
    ).execute()


def save_go_no_go_analysis(rfp_id: str, analysis: GoNoGoAnalysis) -> RfpRecord | None:
    from app.services.go_no_go_service import analysis_activity_note

    now = datetime.now(timezone.utc).isoformat()

    if analysis.insufficient_data:
        fit_score = None
        worth_score = None
        go_no_go = None
        stage = "intake"
        status = "new"
    else:
        fit_score = analysis.fit_score
        worth_score = analysis.worth_score
        go_no_go = analysis.recommendation
        stage = "go_no_go"
        status = "pending_approval" if analysis.recommendation == "review" else "new"
        if analysis.recommendation == "go":
            status = "active"

    client = _get_client()
    client.table("rfps").update(
        {
            "fit_score": fit_score,
            "worth_score": worth_score,
            "go_no_go": go_no_go,
            "stage": stage,
            "status": status,
            "last_activity": now,
            "last_activity_note": analysis_activity_note(analysis),
            "go_no_go_analysis": json.loads(analysis.model_dump_json(by_alias=True)),
        }
    ).or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}").execute()
    return get_rfp(rfp_id)


def clear_go_no_go_analysis(rfp_id: str) -> RfpRecord | None:
    """Remove prior Stage 1 results so a re-run does not show stale scores mid-flight."""
    now = datetime.now(timezone.utc).isoformat()
    client = _get_client()
    client.table("rfps").update(
        {
            "fit_score": None,
            "worth_score": None,
            "go_no_go": None,
            "go_no_go_analysis": None,
            "stage": "go_no_go",
            "status": "new",
            "last_activity": now,
            "last_activity_note": "Go/No-Go re-run in progress — previous analysis cleared.",
        }
    ).or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}").execute()
    return get_rfp(rfp_id)


def mark_rfp_go(rfp_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    client = _get_client()
    client.table("rfps").update(
        {
            "go_no_go": "go",
            "last_activity": now,
            "last_activity_note": "Marked as Go — ready for proposal draft",
        }
    ).or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}").execute()
    updated = get_rfp(rfp_id)
    return updated is not None and updated.go_no_go == "go"


def delete_rfp_row(rfp_id: str) -> bool:
    client = _get_client()
    result = (
        client.table("rfps")
        .delete()
        .or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}")
        .execute()
    )
    rows = _handle_response(result.data, context="delete_rfp")
    return bool(rows)


def get_research_cache(rfp_id: str) -> ProposalResearchCache | None:
    client = _get_client()
    result = (
        client.table("proposal_research")
        .select("payload")
        .eq("rfp_id", rfp_id)
        .limit(1)
        .execute()
    )
    rows = _handle_response(result.data, context="get_research_cache")
    if not rows:
        return None
    payload = rows[0].get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ProposalResearchCache.model_validate(payload)


def save_research_cache(cache: ProposalResearchCache) -> None:
    now = datetime.now(timezone.utc).isoformat()
    cache.updated_at = now
    payload = json.loads(cache.model_dump_json(by_alias=True))
    client = _get_client()
    client.table("proposal_research").upsert(
        {"rfp_id": cache.rfp_id, "payload": payload, "updated_at": now},
        on_conflict="rfp_id",
    ).execute()


def list_google_doc_urls() -> dict[str, str]:
    """Map rfp_id → Google Doc URL from proposal_drafts payloads."""
    client = _get_client()
    result = client.table("proposal_drafts").select("rfp_id,payload").execute()
    rows = _handle_response(result.data, context="list_google_doc_urls")
    out: dict[str, str] = {}
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if not isinstance(payload, dict):
            continue
        url = payload.get("googleDocUrl") or payload.get("google_doc_url")
        rfp_id = row.get("rfp_id")
        if rfp_id and isinstance(url, str) and url.strip():
            out[str(rfp_id)] = url.strip()
    return out


def list_proposal_draft_summaries() -> list[dict[str, Any]]:
    """Lightweight draft rows for dashboard current-proposals / latest draft."""
    client = _get_client()
    result = (
        client.table("proposal_drafts")
        .select("rfp_id,updated_at,payload")
        .order("updated_at", desc=True)
        .execute()
    )
    rows = _handle_response(result.data, context="list_proposal_draft_summaries")
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        sections = payload.get("sections") or []
        if not isinstance(sections, list):
            sections = []
        filled = sum(
            1
            for s in sections
            if isinstance(s, dict) and str(s.get("content") or "").strip()
        )
        updated = row.get("updated_at") or payload.get("updatedAt") or payload.get("updated_at") or ""
        rfp_id = row.get("rfp_id")
        if not rfp_id:
            continue
        out.append(
            {
                "rfp_id": str(rfp_id),
                "updated_at": str(updated or ""),
                "filled_count": filled,
                "section_count": len(sections),
            }
        )
    return out


def touch_rfp_proposal_activity(
    rfp_id: str, *, note: str, at: str | None = None
) -> None:
    """Bump RFP last_activity when a proposal draft is saved."""
    now = at or datetime.now(timezone.utc).isoformat()
    client = _get_client()
    client.table("rfps").update(
        {
            "last_activity": now,
            "last_activity_note": (note or "Proposal draft updated")[:250],
        }
    ).or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}").execute()


def get_proposal_draft(rfp_id: str) -> ProposalDraft | None:
    client = _get_client()
    result = (
        client.table("proposal_drafts")
        .select("payload")
        .eq("rfp_id", rfp_id)
        .limit(1)
        .execute()
    )
    rows = _handle_response(result.data, context="get_proposal_draft")
    if not rows:
        return None
    payload = rows[0].get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ProposalDraft.model_validate(payload)


def save_proposal_draft(draft: ProposalDraft) -> None:
    now = datetime.now(timezone.utc).isoformat()
    draft.updated_at = now
    payload = json.loads(draft.model_dump_json(by_alias=True))
    client = _get_client()
    client.table("proposal_drafts").upsert(
        {"rfp_id": draft.rfp_id, "payload": payload, "updated_at": now},
        on_conflict="rfp_id",
    ).execute()
    sections = draft.sections or []
    filled = sum(1 for s in sections if (s.content or "").strip())
    if filled > 0:
        try:
            touch_rfp_proposal_activity(
                draft.rfp_id,
                note=f"Proposal draft updated — {filled}/{len(sections)} sections filled",
                at=now,
            )
        except Exception as exc:
            logger.warning(
                "touch_rfp_proposal_activity failed for %s: %s", draft.rfp_id, exc
            )


def delete_proposal_draft(rfp_id: str) -> None:
    """Hard-delete the proposal draft row from Supabase for a fresh start."""
    client = _get_client()
    client.table("proposal_drafts").delete().eq("rfp_id", rfp_id).execute()


def delete_research_cache(rfp_id: str) -> None:
    """Hard-delete the research cache (including pipeline checkpoint) from Supabase."""
    client = _get_client()
    client.table("proposal_research").delete().eq("rfp_id", rfp_id).execute()


def save_proposal_draft_archive(
    *,
    rfp_id: str,
    reason: str,
    label: str | None,
    payload: ProposalDraft,
    max_per_rfp: int = 20,
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    sections = payload.sections or []
    section_count = len(sections)
    filled_count = sum(1 for s in sections if (s.content or "").strip())
    body = {
        "rfp_id": rfp_id,
        "archived_at": now,
        "reason": reason,
        "label": label,
        "section_count": section_count,
        "filled_count": filled_count,
        "payload": json.loads(payload.model_dump_json(by_alias=True)),
    }
    client = _get_client()
    result = client.table("proposal_draft_archives").insert(body).execute()
    rows = _handle_response(result.data, context="save_proposal_draft_archive")
    archive_id = str((rows[0] if rows else {}).get("id") or "")
    if not archive_id:
        raise RuntimeError("proposal_draft_archives insert returned no id")

    listed = (
        client.table("proposal_draft_archives")
        .select("id")
        .eq("rfp_id", rfp_id)
        .order("archived_at", desc=True)
        .execute()
    )
    keep_rows = _handle_response(listed.data, context="prune_proposal_draft_archives")
    if len(keep_rows) > max_per_rfp:
        for stale in keep_rows[max_per_rfp:]:
            stale_id = stale.get("id")
            if stale_id:
                client.table("proposal_draft_archives").delete().eq(
                    "id", stale_id
                ).execute()
    return archive_id


def list_proposal_draft_archives(rfp_id: str) -> list[dict[str, Any]]:
    client = _get_client()
    result = (
        client.table("proposal_draft_archives")
        .select(
            "id, rfp_id, archived_at, reason, label, section_count, filled_count"
        )
        .eq("rfp_id", rfp_id)
        .order("archived_at", desc=True)
        .execute()
    )
    rows = _handle_response(result.data, context="list_proposal_draft_archives")
    return [
        {
            "id": str(row.get("id") or ""),
            "rfp_id": str(row.get("rfp_id") or ""),
            "archived_at": str(row.get("archived_at") or ""),
            "reason": str(row.get("reason") or ""),
            "label": row.get("label"),
            "section_count": int(row.get("section_count") or 0),
            "filled_count": int(row.get("filled_count") or 0),
        }
        for row in rows
    ]


def get_proposal_draft_archive(rfp_id: str, archive_id: str) -> ProposalDraft | None:
    client = _get_client()
    result = (
        client.table("proposal_draft_archives")
        .select("payload")
        .eq("rfp_id", rfp_id)
        .eq("id", archive_id)
        .limit(1)
        .execute()
    )
    rows = _handle_response(result.data, context="get_proposal_draft_archive")
    if not rows:
        return None
    payload = rows[0].get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ProposalDraft.model_validate(payload)




def create_sync_job(job_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    client = _get_client()
    client.table("sync_jobs").insert(
        {"id": job_id, "status": "running", "started_at": now}
    ).execute()


def finish_sync_job(
    job_id: str,
    *,
    status: str,
    rfps_found: int,
    pdfs_downloaded: int,
    error: str | None = None,
    rfps_skipped: int = 0,
    rfps_created: int | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    client = _get_client()
    created = rfps_found if rfps_created is None else rfps_created
    base = {
        "status": status,
        "finished_at": now,
        "rfps_found": rfps_found,
        "pdfs_downloaded": pdfs_downloaded,
        "error": error,
    }
    rich = {
        **base,
        "rfps_skipped": int(rfps_skipped or 0),
        "rfps_created": int(created),
    }
    try:
        client.table("sync_jobs").update(rich).eq("id", job_id).execute()
    except Exception:
        # Older DBs without rfps_skipped / rfps_created columns.
        # Encode extras in error only when the job succeeded (error is empty).
        if error is None and (rfps_skipped or created != rfps_found):
            import json

            base["error"] = (
                "ZO_SYNC_META:"
                + json.dumps(
                    {"rfpsSkipped": int(rfps_skipped or 0), "rfpsCreated": int(created)}
                )
            )
        client.table("sync_jobs").update(base).eq("id", job_id).execute()


# uvicorn --reload kills in-flight Playwright tasks but leaves sync_jobs.status=
# "running", so the modal spins forever. Expire zombies on read/trigger.
_STALE_SYNC_JOB_MINUTES = 8


def expire_stale_running_sync_jobs(
    *,
    max_age_minutes: int = _STALE_SYNC_JOB_MINUTES,
) -> list[str]:
    """Mark long-running sync jobs failed. Returns expired job ids."""
    client = _get_client()
    result = (
        client.table("sync_jobs")
        .select("id,started_at")
        .eq("status", "running")
        .execute()
    )
    rows = _handle_response(result.data, context="expire_stale_running_sync_jobs")
    if not rows:
        return []

    now = datetime.now(timezone.utc)
    expired: list[str] = []
    for row in rows:
        started_raw = row.get("started_at") or ""
        try:
            started = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except ValueError:
            started = now - timedelta(minutes=max_age_minutes + 1)
        age_min = (now - started).total_seconds() / 60.0
        if age_min < max_age_minutes:
            continue
        job_id = str(row.get("id") or "")
        if not job_id:
            continue
        finish_sync_job(
            job_id,
            status="failed",
            rfps_found=0,
            pdfs_downloaded=0,
            error=(
                f"Sync timed out after {int(age_min)} minutes "
                "(often caused by server reload killing Playwright). Retry sync."
            ),
        )
        expired.append(job_id)
    return expired


def get_latest_sync_job() -> dict[str, Any] | None:
    def _read() -> dict[str, Any] | None:
        expire_stale_running_sync_jobs()
        client = _get_client()
        result = (
            client.table("sync_jobs")
            .select("*")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = _handle_response(result.data, context="get_latest_sync_job")
        return rows[0] if rows else None

    return _with_transient_retry("get_latest_sync_job", _read)


def get_running_sync_job() -> dict[str, Any] | None:
    def _read() -> dict[str, Any] | None:
        expire_stale_running_sync_jobs()
        client = _get_client()
        result = (
            client.table("sync_jobs")
            .select("*")
            .eq("status", "running")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = _handle_response(result.data, context="get_running_sync_job")
        return rows[0] if rows else None

    return _with_transient_retry("get_running_sync_job", _read)
