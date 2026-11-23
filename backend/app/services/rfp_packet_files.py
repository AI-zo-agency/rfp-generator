"""Cite-linked supplemental PDFs beside the primary RFP solicitation."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from app.models.rfp_packet_file import RfpPacketFile
from app.services import rfp_repository as repo
from app.services import rfp_storage
from app.services import supabase_db as sb

logger = logging.getLogger(__name__)


def _use_supabase() -> bool:
    return sb.use_supabase_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ensure_packet_files_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rfp_packet_files (
            id TEXT PRIMARY KEY,
            rfp_id TEXT NOT NULL,
            cite_target TEXT NOT NULL,
            cite_quote TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            storage_path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL DEFAULT '',
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT,
            UNIQUE (rfp_id, cite_target)
        );
        CREATE INDEX IF NOT EXISTS rfp_packet_files_rfp_id_idx
            ON rfp_packet_files (rfp_id);
        """
    )


def _row_to_file(row: dict | sqlite3.Row) -> RfpPacketFile:
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    else:
        data = dict(row)
    return RfpPacketFile(
        id=str(data["id"]),
        rfpId=str(data["rfp_id"]),
        citeTarget=str(data["cite_target"]),
        citeQuote=str(data.get("cite_quote") or ""),
        filename=str(data.get("filename") or ""),
        storagePath=str(data["storage_path"]),
        contentSha256=str(data.get("content_sha256") or ""),
        uploadedAt=str(data["uploaded_at"]),
        uploadedBy=(str(data["uploaded_by"]) if data.get("uploaded_by") else None),
    )


def _raise_if_table_missing(exc: Exception) -> None:
    message = str(exc)
    if "rfp_packet_files" in message and (
        "schema cache" in message or "does not exist" in message or "PGRST205" in message
    ):
        raise RuntimeError(
            "Supabase table rfp_packet_files is missing. Run "
            "backend/supabase/migrations/20261119_rfp_packet_files.sql "
            "in the Supabase SQL editor, then retry."
        ) from exc


def list_packet_files(rfp_id: str) -> list[RfpPacketFile]:
    rid = (rfp_id or "").strip()
    if not rid:
        return []
    if _use_supabase():
        client = sb._get_client()  # noqa: SLF001
        try:
            result = (
                client.table("rfp_packet_files")
                .select("*")
                .eq("rfp_id", rid)
                .order("uploaded_at")
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            _raise_if_table_missing(exc)
            raise
        rows = result.data or []
        return [_row_to_file(row) for row in rows if isinstance(row, dict)]

    with repo._connect() as conn:  # noqa: SLF001
        ensure_packet_files_table(conn)
        rows = conn.execute(
            "SELECT * FROM rfp_packet_files WHERE rfp_id = ? ORDER BY uploaded_at ASC",
            (rid,),
        ).fetchall()
    return [_row_to_file(row) for row in rows]


def get_packet_file(rfp_id: str, file_id: str) -> RfpPacketFile | None:
    rid = (rfp_id or "").strip()
    fid = (file_id or "").strip()
    if not rid or not fid:
        return None
    if _use_supabase():
        client = sb._get_client()  # noqa: SLF001
        result = (
            client.table("rfp_packet_files")
            .select("*")
            .eq("rfp_id", rid)
            .eq("id", fid)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return _row_to_file(rows[0])

    with repo._connect() as conn:  # noqa: SLF001
        ensure_packet_files_table(conn)
        row = conn.execute(
            "SELECT * FROM rfp_packet_files WHERE rfp_id = ? AND id = ?",
            (rid, fid),
        ).fetchone()
    return _row_to_file(row) if row else None


def _find_by_cite(rfp_id: str, cite_target: str) -> RfpPacketFile | None:
    target = (cite_target or "").strip()
    if not target:
        return None
    for item in list_packet_files(rfp_id):
        if item.cite_target == target:
            return item
    return None


def get_packet_file_by_cite(rfp_id: str, cite_target: str) -> RfpPacketFile | None:
    """Return the packet file for this cite on this RFP only (never cross-RFP)."""
    return _find_by_cite(rfp_id, cite_target)


def upsert_packet_file(
    rfp_id: str,
    *,
    cite_target: str,
    cite_quote: str,
    filename: str,
    content: bytes,
    uploaded_by: str | None = None,
) -> RfpPacketFile:
    rid = (rfp_id or "").strip()
    target = (cite_target or "").strip()
    if not rid:
        raise ValueError("rfp_id is required")
    if not target:
        raise ValueError("cite_target is required")

    existing = _find_by_cite(rid, target)
    if existing:
        delete_packet_file(rid, existing.id)

    file_id = str(uuid.uuid4())
    storage_path = rfp_storage.save_rfp_packet_pdf(rid, file_id, content)
    record = RfpPacketFile(
        id=file_id,
        rfpId=rid,
        citeTarget=target,
        citeQuote=(cite_quote or "").strip(),
        filename=(filename or "exhibit.pdf").strip() or "exhibit.pdf",
        storagePath=storage_path,
        contentSha256=_sha256(content),
        uploadedAt=_now_iso(),
        uploadedBy=(uploaded_by or None),
    )
    _insert_row(record)
    logger.info(
        "packet file upserted rfp_id=%s cite=%s file_id=%s",
        rid,
        target,
        file_id,
    )
    return record


def _insert_row(record: RfpPacketFile) -> None:
    row = {
        "id": record.id,
        "rfp_id": record.rfp_id,
        "cite_target": record.cite_target,
        "cite_quote": record.cite_quote,
        "filename": record.filename,
        "storage_path": record.storage_path,
        "content_sha256": record.content_sha256,
        "uploaded_at": record.uploaded_at,
        "uploaded_by": record.uploaded_by,
    }
    if _use_supabase():
        client = sb._get_client()  # noqa: SLF001
        try:
            client.table("rfp_packet_files").upsert(
                row, on_conflict="rfp_id,cite_target"
            ).execute()
        except Exception as exc:  # noqa: BLE001
            _raise_if_table_missing(exc)
            raise
        return

    with repo._connect() as conn:  # noqa: SLF001
        ensure_packet_files_table(conn)
        conn.execute(
            """
            INSERT INTO rfp_packet_files (
                id, rfp_id, cite_target, cite_quote, filename,
                storage_path, content_sha256, uploaded_at, uploaded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.rfp_id,
                record.cite_target,
                record.cite_quote,
                record.filename,
                record.storage_path,
                record.content_sha256,
                record.uploaded_at,
                record.uploaded_by,
            ),
        )


def delete_packet_file(rfp_id: str, file_id: str) -> bool:
    rid = (rfp_id or "").strip()
    fid = (file_id or "").strip()
    if not rid or not fid:
        return False
    existing = get_packet_file(rid, fid)
    if not existing:
        return False

    rfp_storage.delete_rfp_packet_pdf(rid, existing.storage_path)

    if _use_supabase():
        client = sb._get_client()  # noqa: SLF001
        client.table("rfp_packet_files").delete().eq("rfp_id", rid).eq("id", fid).execute()
    else:
        with repo._connect() as conn:  # noqa: SLF001
            ensure_packet_files_table(conn)
            conn.execute(
                "DELETE FROM rfp_packet_files WHERE rfp_id = ? AND id = ?",
                (rid, fid),
            )
    logger.info("packet file deleted rfp_id=%s file_id=%s", rid, fid)
    return True
