import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar

import httpx

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.services import supabase_db as sb
from app.services.rfp_repository import _connect, init_db as init_rfp_db

logger = logging.getLogger(__name__)
T = TypeVar("T")
_SUPABASE_READ_RETRIES = 6
_SUPABASE_WRITE_RETRIES = 6
_RETRY_BACKOFF_SEC = (0.25, 0.6, 1.2, 2.0, 3.0, 4.5)
_TRANSIENT_EXC = (httpx.HTTPError, OSError, ConnectionError, TimeoutError)


def _use_supabase() -> bool:
    return sb.use_supabase_db()


def _with_supabase_retry(
    op_name: str,
    fn: Callable[[], T],
    *,
    retries: int,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except _TRANSIENT_EXC as exc:
            last_exc = exc
            if attempt >= retries - 1:
                raise
            if _use_supabase():
                sb.reset_supabase_client()
            delay = _RETRY_BACKOFF_SEC[min(attempt, len(_RETRY_BACKOFF_SEC) - 1)]
            logger.warning(
                "Supabase transient failure in %s (attempt %d/%d): %s",
                op_name,
                attempt + 1,
                retries,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def init_proposal_db() -> None:
    init_rfp_db()
    if _use_supabase():
        return
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS proposal_research (
                rfp_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposal_drafts (
                rfp_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def get_research_cache(rfp_id: str) -> ProposalResearchCache | None:
    if _use_supabase():
        return _with_supabase_retry(
            "get_research_cache",
            lambda: sb.get_research_cache(rfp_id),
            retries=_SUPABASE_READ_RETRIES,
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM proposal_research WHERE rfp_id = ?",
            (rfp_id,),
        ).fetchone()
    if not row:
        return None
    return ProposalResearchCache.model_validate(json.loads(row["payload"]))


def save_research_cache(cache: ProposalResearchCache) -> None:
    if _use_supabase():
        _with_supabase_retry(
            "save_research_cache",
            lambda: sb.save_research_cache(cache),
            retries=_SUPABASE_WRITE_RETRIES,
        )
        return
    now = datetime.now(timezone.utc).isoformat()
    cache.updated_at = now
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO proposal_research (rfp_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(rfp_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (cache.rfp_id, cache.model_dump_json(by_alias=True), now),
        )


def get_proposal_draft(rfp_id: str) -> ProposalDraft | None:
    if _use_supabase():
        return _with_supabase_retry(
            "get_proposal_draft",
            lambda: sb.get_proposal_draft(rfp_id),
            retries=_SUPABASE_READ_RETRIES,
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM proposal_drafts WHERE rfp_id = ?",
            (rfp_id,),
        ).fetchone()
    if not row:
        return None
    return ProposalDraft.model_validate(json.loads(row["payload"]))


def save_proposal_draft(draft: ProposalDraft) -> None:
    if _use_supabase():
        _with_supabase_retry(
            "save_proposal_draft",
            lambda: sb.save_proposal_draft(draft),
            retries=_SUPABASE_WRITE_RETRIES,
        )
        return
    now = datetime.now(timezone.utc).isoformat()
    draft.updated_at = now
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO proposal_drafts (rfp_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(rfp_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (draft.rfp_id, draft.model_dump_json(by_alias=True), now),
        )


async def aget_research_cache(rfp_id: str) -> ProposalResearchCache | None:
    return await asyncio.to_thread(get_research_cache, rfp_id)


async def asave_research_cache(cache: ProposalResearchCache) -> None:
    await asyncio.to_thread(save_research_cache, cache)


async def aget_proposal_draft(rfp_id: str) -> ProposalDraft | None:
    return await asyncio.to_thread(get_proposal_draft, rfp_id)


async def asave_proposal_draft(draft: ProposalDraft) -> None:
    await asyncio.to_thread(save_proposal_draft, draft)


def list_google_doc_urls() -> dict[str, str]:
    """Map rfp_id → Google Doc URL from saved proposal drafts."""
    if _use_supabase():
        return _with_supabase_retry(
            "list_google_doc_urls",
            lambda: sb.list_google_doc_urls(),
            retries=_SUPABASE_READ_RETRIES,
        )
    out: dict[str, str] = {}
    with _connect() as conn:
        rows = conn.execute("SELECT rfp_id, payload FROM proposal_drafts").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except Exception:
            continue
        url = payload.get("googleDocUrl") or payload.get("google_doc_url")
        if isinstance(url, str) and url.strip():
            out[str(row["rfp_id"])] = url.strip()
    return out


def delete_proposal_draft(rfp_id: str) -> None:
    """Hard-delete the proposal draft from DB so Reset starts completely fresh."""
    if _use_supabase():
        _with_supabase_retry(
            "delete_proposal_draft",
            lambda: sb.delete_proposal_draft(rfp_id),
            retries=_SUPABASE_WRITE_RETRIES,
        )
        return
    with _connect() as conn:
        conn.execute("DELETE FROM proposal_drafts WHERE rfp_id = ?", (rfp_id,))


def delete_research_cache(rfp_id: str) -> None:
    """Hard-delete the research cache (pipeline checkpoint, evidence, etc.) from DB."""
    if _use_supabase():
        _with_supabase_retry(
            "delete_research_cache",
            lambda: sb.delete_research_cache(rfp_id),
            retries=_SUPABASE_WRITE_RETRIES,
        )
        return
    with _connect() as conn:
        conn.execute("DELETE FROM proposal_research WHERE rfp_id = ?", (rfp_id,))


async def adelete_proposal_draft(rfp_id: str) -> None:
    await asyncio.to_thread(delete_proposal_draft, rfp_id)


async def adelete_research_cache(rfp_id: str) -> None:
    await asyncio.to_thread(delete_research_cache, rfp_id)
