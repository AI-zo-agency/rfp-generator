import json
import sqlite3
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.services import supabase_db as sb
from app.services.rfp_repository import _connect, init_db as init_rfp_db


def _use_supabase() -> bool:
    return sb.use_supabase_db()


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
        return sb.get_research_cache(rfp_id)
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
        sb.save_research_cache(cache)
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
        return sb.get_proposal_draft(rfp_id)
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
        sb.save_proposal_draft(draft)
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
