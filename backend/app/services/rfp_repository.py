import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.models.go_no_go import GoNoGoAnalysis
from app.models.rfp import DashboardStats, ManualRfpCreate, RfpRecord
from app.services.rfp_storage import delete_rfp_pdf, save_rfp_pdf
from app.services import supabase_db as sb

TERMINAL_STATUSES = {"won", "lost", "passed", "submitted"}


def _use_supabase() -> bool:
    return sb.use_supabase_db()


def _db_path() -> Path:
    path = settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    if _use_supabase():
        try:
            sb.ping()
        except sb.SupabaseDbError as exc:
            import logging

            logging.getLogger(__name__).warning("Supabase ping failed: %s", exc)
        return
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rfps (
                id TEXT PRIMARY KEY,
                external_id TEXT UNIQUE,
                title TEXT NOT NULL,
                client TEXT,
                source TEXT DEFAULT 'justwin',
                sector TEXT,
                location TEXT,
                due_date TEXT,
                received_date TEXT,
                stage TEXT DEFAULT 'intake',
                status TEXT DEFAULT 'new',
                priority TEXT DEFAULT 'medium',
                fit_score INTEGER,
                worth_score INTEGER,
                go_no_go TEXT,
                assigned_to TEXT,
                estimated_value INTEGER,
                page_limit INTEGER,
                last_activity TEXT,
                last_activity_note TEXT,
                contract_role TEXT DEFAULT 'prime',
                description TEXT,
                justwin_tab TEXT,
                pdf_path TEXT,
                justwin_detail_url TEXT,
                synced_at TEXT,
                go_no_go_analysis TEXT
            );
            """
        )
        _ensure_column(conn, "rfps", "go_no_go_analysis", "TEXT")


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    column_type: str,
) -> None:
    columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _parse_analysis_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _row_to_rfp(row: sqlite3.Row) -> RfpRecord:
    rfp_id = row["id"]
    pdf_path = row["pdf_path"]
    return RfpRecord(
        id=rfp_id,
        externalId=row["external_id"],
        title=row["title"],
        client=row["client"] or "",
        source=row["source"] or "justwin",
        sector=row["sector"] or "Public Sector",
        location=row["location"] or "",
        dueDate=row["due_date"] or "",
        receivedDate=row["received_date"] or "",
        stage=row["stage"] or "intake",
        status=row["status"] or "new",
        priority=row["priority"] or "medium",
        fitScore=row["fit_score"],
        worthScore=row["worth_score"],
        goNoGo=row["go_no_go"],
        assignedTo=row["assigned_to"],
        estimatedValue=row["estimated_value"],
        pageLimit=row["page_limit"],
        lastActivity=row["last_activity"] or "",
        lastActivityNote=row["last_activity_note"] or "",
        contractRole=row["contract_role"] or "prime",
        description=row["description"],
        justwinTab=row["justwin_tab"],
        pdfPath=pdf_path,
        justwinDetailUrl=row["justwin_detail_url"],
        syncedAt=row["synced_at"],
        goNoGoAnalysis=_parse_analysis_json(
            row["go_no_go_analysis"] if "go_no_go_analysis" in row.keys() else None
        ),
        pdfUrl=None,
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


def list_rfps() -> list[RfpRecord]:
    if _use_supabase():
        return sb.list_rfps()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rfps ORDER BY synced_at DESC, received_date DESC"
        ).fetchall()
    return _dedupe_by_title([_row_to_rfp(row) for row in rows])


def get_rfp(rfp_id: str) -> RfpRecord | None:
    if _use_supabase():
        return sb.get_rfp(rfp_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rfps WHERE id = ? OR external_id = ?",
            (rfp_id, rfp_id),
        ).fetchone()
    return _row_to_rfp(row) if row else None


def rfp_exists(rfp_id: str) -> bool:
    if _use_supabase():
        return sb.rfp_exists(rfp_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM rfps WHERE id = ? OR external_id = ? LIMIT 1",
            (rfp_id, rfp_id),
        ).fetchone()
    return row is not None


def get_rfp_pdf_path(rfp_id: str) -> str | None:
    if _use_supabase():
        return sb.get_rfp_pdf_path(rfp_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT pdf_path FROM rfps WHERE id = ? OR external_id = ?",
            (rfp_id, rfp_id),
        ).fetchone()
    return row["pdf_path"] if row and row["pdf_path"] else None


def insert_manual_rfp(payload: ManualRfpCreate, pdf_path: str | None = None) -> RfpRecord:
    now = datetime.now(timezone.utc).isoformat()
    rfp_id = f"manual-{uuid.uuid4()}"

    record = RfpRecord(
        id=rfp_id,
        externalId=rfp_id,
        title=payload.title.strip(),
        client=payload.client.strip(),
        source="manual",
        sector=payload.sector.strip() or "Public Sector",
        location=payload.location.strip(),
        dueDate=payload.due_date,
        receivedDate=now[:10],
        stage="intake",
        status="new",
        priority=payload.priority,
        fitScore=None,
        worthScore=None,
        goNoGo=None,
        assignedTo=None,
        estimatedValue=payload.estimated_value,
        pageLimit=payload.page_limit,
        lastActivity=now,
        lastActivityNote="Manually added",
        contractRole="prime",
        description=payload.description.strip() if payload.description else None,
        syncedAt=now,
        pdfPath=pdf_path,
        pdfUrl=None,
    )

    if _use_supabase():
        return sb.insert_rfp(record)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rfps (
                id, external_id, title, client, source, sector, location,
                due_date, received_date, stage, status, priority, fit_score, worth_score,
                go_no_go, assigned_to, estimated_value, page_limit, last_activity,
                last_activity_note, contract_role, description, justwin_tab, pdf_path,
                justwin_detail_url, synced_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                record.id,
                record.external_id,
                record.title,
                record.client,
                record.source,
                record.sector,
                record.location,
                record.due_date,
                record.received_date,
                record.stage,
                record.status,
                record.priority,
                record.fit_score,
                record.worth_score,
                record.go_no_go,
                record.assigned_to,
                record.estimated_value,
                record.page_limit,
                record.last_activity,
                record.last_activity_note,
                record.contract_role,
                record.description,
                record.justwin_tab,
                record.pdf_path,
                record.justwin_detail_url,
                record.synced_at,
            ),
        )
    return record


def upsert_rfp(record: RfpRecord) -> None:
    if _use_supabase():
        sb.upsert_rfp(record)
        return

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rfps (
                id, external_id, title, client, source, sector, location,
                due_date, received_date, stage, status, priority, fit_score, worth_score,
                go_no_go, assigned_to, estimated_value, page_limit, last_activity,
                last_activity_note, contract_role, description, justwin_tab, pdf_path,
                justwin_detail_url, synced_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT(external_id) DO UPDATE SET
                title = excluded.title,
                client = excluded.client,
                due_date = excluded.due_date,
                received_date = excluded.received_date,
                fit_score = excluded.fit_score,
                description = excluded.description,
                justwin_tab = excluded.justwin_tab,
                pdf_path = COALESCE(excluded.pdf_path, rfps.pdf_path),
                last_activity = excluded.last_activity,
                last_activity_note = excluded.last_activity_note,
                synced_at = excluded.synced_at
            """,
            (
                record.id,
                record.external_id or record.id,
                record.title,
                record.client,
                record.source,
                record.sector,
                record.location,
                record.due_date,
                record.received_date,
                record.stage,
                record.status,
                record.priority,
                record.fit_score,
                record.worth_score,
                record.go_no_go,
                record.assigned_to,
                record.estimated_value,
                record.page_limit,
                record.last_activity,
                record.last_activity_note,
                record.contract_role,
                record.description,
                record.justwin_tab,
                record.pdf_path,
                record.justwin_detail_url,
                record.synced_at,
            ),
        )


def _composite_go_score(
    fit_score: int | None, worth_score: int | None
) -> int | None:
    if fit_score is None and worth_score is None:
        return None
    if fit_score is not None and worth_score is not None:
        return round((fit_score + worth_score) / 2)
    return fit_score if fit_score is not None else worth_score


def save_go_no_go_analysis(rfp_id: str, analysis: GoNoGoAnalysis) -> RfpRecord | None:
    if _use_supabase():
        return sb.save_go_no_go_analysis(rfp_id, analysis)

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

    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE rfps
            SET fit_score = ?,
                worth_score = ?,
                go_no_go = ?,
                stage = ?,
                status = ?,
                last_activity = ?,
                last_activity_note = ?,
                go_no_go_analysis = ?
            WHERE id = ? OR external_id = ?
            """,
            (
                fit_score,
                worth_score,
                go_no_go,
                stage,
                status,
                now,
                analysis_activity_note(analysis),
                analysis.model_dump_json(by_alias=True),
                rfp_id,
                rfp_id,
            ),
        )
        if cursor.rowcount == 0:
            return None

    return get_rfp(rfp_id)


def mark_rfp_go(rfp_id: str) -> bool:
    if _use_supabase():
        return sb.mark_rfp_go(rfp_id)

    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE rfps
            SET go_no_go = ?, last_activity = ?, last_activity_note = ?
            WHERE id = ? OR external_id = ?
            """,
            ("go", now, "Marked as Go — ready for proposal draft", rfp_id, rfp_id),
        )
    return cursor.rowcount > 0


def compute_stats(all_rfps: list[RfpRecord]) -> DashboardStats:
    active = [r for r in all_rfps if r.status not in TERMINAL_STATUSES]
    now = datetime.now(timezone.utc)
    due_this_week = 0
    for rfp in active:
        try:
            due = datetime.fromisoformat(rfp.due_date.replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            days = (due - now).days
            if 0 <= days <= 7:
                due_this_week += 1
        except ValueError:
            continue

    wins = sum(1 for r in all_rfps if r.status == "won")
    closed = sum(1 for r in all_rfps if r.status in {"won", "lost"})
    fit_scores = [r.fit_score for r in all_rfps if r.fit_score is not None]
    pipeline_value = sum(r.estimated_value or 0 for r in active)

    return DashboardStats(
        activeRfps=len(active),
        pendingGoNoGo=sum(1 for r in active if r.go_no_go is None),
        inProgress=sum(1 for r in active if r.status == "in_progress"),
        dueThisWeek=due_this_week,
        submittedThisMonth=sum(1 for r in all_rfps if r.status == "submitted"),
        winRate=round((wins / closed) * 100) if closed else 0,
        pipelineValue=pipeline_value,
        avgFitScore=round(sum(fit_scores) / len(fit_scores)) if fit_scores else 0,
    )


def save_manual_pdf(rfp_id: str, content: bytes) -> str:
    return save_rfp_pdf(rfp_id, content)


def update_rfp_pdf_path(rfp_id: str, pdf_path: str) -> None:
    if _use_supabase():
        sb.update_rfp_pdf_path(rfp_id, pdf_path)
        return

    with _connect() as conn:
        conn.execute(
            "UPDATE rfps SET pdf_path = ? WHERE id = ? OR external_id = ?",
            (pdf_path, rfp_id, rfp_id),
        )


def delete_rfp(rfp_id: str) -> RfpRecord | None:
    rfp = get_rfp(rfp_id)
    if not rfp:
        return None

    pdf_path = rfp.pdf_path or get_rfp_pdf_path(rfp.id)

    if _use_supabase():
        if not sb.delete_rfp_row(rfp.id):
            return None
        delete_rfp_pdf(rfp.id, pdf_path)
        return rfp

    with _connect() as conn:
        conn.execute("DELETE FROM proposal_research WHERE rfp_id = ?", (rfp.id,))
        conn.execute("DELETE FROM proposal_drafts WHERE rfp_id = ?", (rfp.id,))
        cursor = conn.execute(
            "DELETE FROM rfps WHERE id = ? OR external_id = ?",
            (rfp.id, rfp.id),
        )
        if cursor.rowcount == 0:
            return None

    delete_rfp_pdf(rfp.id, pdf_path)
    return rfp
