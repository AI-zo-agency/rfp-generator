"""Create a Google Doc populated with the full proposal manuscript."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from googleapiclient.errors import HttpError

from app.models.proposal import ProposalDraft
from app.services import google_oauth
from app.services.google_drive import is_configured
from app.services.proposal_manuscript import (
    build_google_doc_export_blocks,
    build_manuscript_structured,
)

logger = logging.getLogger(__name__)

_HEADING_STYLES = {
    1: "HEADING_1",
    2: "HEADING_2",
    3: "HEADING_3",
}

_INSERT_CHUNK_CHARS = 45_000
_STYLE_BATCH_SIZE = 50
_RATE_LIMIT_SLEEP_SEC = 65
# Beyond this many native tables, remaining tables export as formatted text (much faster).
_MAX_NATIVE_TABLES = 24


class ProposalGoogleDocExportError(Exception):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _sanitize_doc_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s\-–—|&.,()]+", "", title or "").strip()
    return (cleaned[:120] or "Proposal").strip()


def _drive_service():
    if not is_configured():
        raise ProposalGoogleDocExportError(
            "Google is not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "and GOOGLE_REFRESH_TOKEN in backend/.env (run scripts/google_oauth_setup.py).",
            status_code=503,
        )
    try:
        return google_oauth.build_drive_service()
    except google_oauth.GoogleOAuthError as exc:
        raise ProposalGoogleDocExportError(str(exc), status_code=503) from exc


def _docs_service():
    try:
        return google_oauth.build_docs_service()
    except google_oauth.GoogleOAuthError as exc:
        raise ProposalGoogleDocExportError(str(exc), status_code=503) from exc


def _http_error_message(exc: HttpError, *, fallback: str) -> str:
    detail = str(exc)
    if exc.resp.status == 429 or "RATE_LIMIT_EXCEEDED" in detail or "Quota exceeded" in detail:
        return (
            "Google Docs rate limit reached (about 60 updates per minute). "
            "Wait one minute, then export again."
        )
    if "SERVICE_DISABLED" in detail or "has not been used" in detail or "is disabled" in detail:
        if "docs.googleapis.com" in detail:
            return (
                "Google Docs API is disabled for this Cloud project. Enable it here, "
                "wait 1–2 minutes, then try export again: "
                "https://console.developers.google.com/apis/api/docs.googleapis.com/overview?project=924544080737"
            )
        if "drive.googleapis.com" in detail:
            return (
                "Google Drive API is disabled for this Cloud project. Enable it here, "
                "wait 1–2 minutes, then try export again: "
                "https://console.developers.google.com/apis/api/drive.googleapis.com/overview?project=924544080737"
            )
        return (
            "A required Google API is disabled in Cloud Console. Enable Google Docs API "
            "(and Drive API if prompted), wait a few minutes, then retry export."
        )
    if exc.resp.status in (401, 403):
        return (
            "Google permission denied. Re-run backend/scripts/google_oauth_setup.py "
            "to grant Drive + Docs write access, then update GOOGLE_REFRESH_TOKEN."
        )
    return f"{fallback}: {detail}"


def _batch_update_with_retry(
    docs_service: Any,
    document_id: str,
    requests: list[dict[str, Any]],
    *,
    max_retries: int = 2,
) -> None:
    if not requests:
        return
    for i in range(0, len(requests), 80):
        chunk = requests[i : i + 80]
        attempt = 0
        while True:
            try:
                docs_service.documents().batchUpdate(
                    documentId=document_id,
                    body={"requests": chunk},
                ).execute()
                break
            except HttpError as exc:
                if exc.resp.status == 429 and attempt < max_retries:
                    attempt += 1
                    logger.warning(
                        "Google Docs rate limit; sleeping %ss (attempt %s)",
                        _RATE_LIMIT_SLEEP_SEC,
                        attempt,
                    )
                    time.sleep(_RATE_LIMIT_SLEEP_SEC)
                    continue
                raise


def _insert_text_at(
    docs_service: Any,
    document_id: str,
    text: str,
    *,
    index: int,
) -> int:
    if not text:
        return index
    for offset in range(0, len(text), _INSERT_CHUNK_CHARS):
        piece = text[offset : offset + _INSERT_CHUNK_CHARS]
        _batch_update_with_retry(
            docs_service,
            document_id,
            [{"insertText": {"location": {"index": index}, "text": piece}}],
        )
        index += len(piece)
    return index


def _doc_body_end(docs_service: Any, document_id: str) -> int:
    doc = docs_service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    if not content:
        return 1
    return int(content[-1]["endIndex"]) - 1


def _clear_document_body(docs_service: Any, document_id: str) -> None:
    end = _doc_body_end(docs_service, document_id)
    if end <= 1:
        return
    _batch_update_with_retry(
        docs_service,
        document_id,
        [
            {
                "deleteContentRange": {
                    "range": {"startIndex": 1, "endIndex": end},
                }
            }
        ],
    )


def _insert_table_at(
    docs_service: Any,
    document_id: str,
    *,
    index: int,
    headers: list[str],
    rows: list[list[str]],
) -> int:
    cols = max(len(headers), 1)
    data = [headers] + rows
    data = [(list(row) + [""] * cols)[:cols] for row in data]
    nrows = len(data)

    _batch_update_with_retry(
        docs_service,
        document_id,
        [
            {
                "insertTable": {
                    "rows": nrows,
                    "columns": cols,
                    "location": {"index": index},
                }
            }
        ],
    )

    doc = docs_service.documents().get(documentId=document_id).execute()
    table_el = None
    best_start = -1
    for el in doc.get("body", {}).get("content", []):
        if "table" not in el:
            continue
        si = int(el.get("startIndex", 0))
        if si >= index and si > best_start:
            best_start = si
            table_el = el
    if not table_el:
        return index + 1

    table = table_el["table"]
    cell_writes: list[tuple[int, str, bool]] = []
    for r_idx, row in enumerate(data):
        table_row = table["tableRows"][r_idx]
        is_header = r_idx == 0
        for c_idx, cell_text in enumerate(row):
            if not (cell_text or "").strip():
                continue
            cell = table_row["tableCells"][c_idx]
            cell_content = cell.get("content") or []
            if not cell_content:
                continue
            start = int(cell_content[0].get("startIndex", 0))
            cell_writes.append((start, str(cell_text), is_header))

    cell_writes.sort(key=lambda item: item[0], reverse=True)
    insert_reqs = [
        {"insertText": {"location": {"index": start}, "text": text}}
        for start, text, _ in cell_writes
    ]
    _batch_update_with_retry(docs_service, document_id, insert_reqs)
    return _doc_body_end(docs_service, document_id)


def _apply_style_spans(
    docs_service: Any,
    document_id: str,
    spans: list[tuple[int, int, int, bool, bool]],
) -> None:
    style_reqs: list[dict[str, Any]] = []
    for start, end, heading, bold, italic in spans:
        if end <= start:
            continue
        if heading:
            named = _HEADING_STYLES.get(heading, "HEADING_2")
            style_reqs.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "paragraphStyle": {"namedStyleType": named},
                        "fields": "namedStyleType",
                    }
                }
            )
        text_end = max(start, end - 1)
        if bold or italic:
            style: dict[str, Any] = {}
            fields: list[str] = []
            if bold:
                style["bold"] = True
                fields.append("bold")
            if italic:
                style["italic"] = True
                fields.append("italic")
            style_reqs.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": text_end},
                        "textStyle": style,
                        "fields": ",".join(fields),
                    }
                }
            )

    for i in range(0, len(style_reqs), _STYLE_BATCH_SIZE):
        batch = style_reqs[i : i + _STYLE_BATCH_SIZE]
        _batch_update_with_retry(docs_service, document_id, batch)


def _format_table_as_text(headers: list[str], rows: list[list[str]]) -> str:
    cols = max(len(headers), 1)
    hdr = (headers + [""] * cols)[:cols]
    lines = [" | ".join(hdr)]
    for row in rows:
        padded = (list(row) + [""] * cols)[:cols]
        lines.append(" | ".join(padded))
    return "\n".join(lines) + "\n\n"


def _write_blocks_to_doc(
    docs_service: Any,
    document_id: str,
    blocks: list[dict[str, Any]],
) -> None:
    index = 1
    doc_spans: list[tuple[int, int, int, bool, bool]] = []

    for block in blocks:
        kind = block.get("kind")
        if kind != "text":
            continue
        text = block.get("text") or ""
        if not text:
            continue
        base = index
        for span in block.get("spans") or []:
            if len(span) >= 5:
                doc_spans.append(
                    (base + span[0], base + span[1], span[2], span[3], span[4])
                )
            elif len(span) >= 4:
                doc_spans.append((base + span[0], base + span[1], span[2], span[3], False))
        index = _insert_text_at(docs_service, document_id, text, index=index)

    _apply_style_spans(docs_service, document_id, doc_spans)


def _export_sync(
    *,
    doc_title: str,
    draft: ProposalDraft,
    existing_document_id: str | None = None,
    existing_document_url: str | None = None,
) -> dict[str, Any]:
    sections = build_manuscript_structured(draft.sections)
    if not sections:
        raise ProposalGoogleDocExportError(
            "No proposal content to export. Generate sections first.",
            status_code=400,
        )

    blocks = build_google_doc_export_blocks(doc_title, sections)

    drive = _drive_service()
    docs = _docs_service()

    document_id = (existing_document_id or "").strip()
    web_link = (existing_document_url or "").strip()

    if document_id:
        try:
            _clear_document_body(docs, document_id)
            try:
                drive.files().update(
                    fileId=document_id,
                    body={"name": doc_title},
                ).execute()
            except HttpError:
                pass
        except HttpError as exc:
            logger.warning("Could not reuse Google Doc %s: %s", document_id, exc)
            document_id = ""
            web_link = ""

    if not document_id:
        try:
            created = (
                drive.files()
                .create(
                    body={
                        "name": doc_title,
                        "mimeType": "application/vnd.google-apps.document",
                    },
                    fields="id,webViewLink",
                )
                .execute()
            )
        except HttpError as exc:
            logger.warning("Google Drive create failed: %s", exc)
            raise ProposalGoogleDocExportError(
                _http_error_message(exc, fallback="Could not create Google Doc"),
                status_code=403 if exc.resp.status in (401, 403) else 502,
            ) from exc

        document_id = created["id"]
        web_link = (
            created.get("webViewLink")
            or f"https://docs.google.com/document/d/{document_id}/edit"
        )

    try:
        _write_blocks_to_doc(docs, document_id, blocks)
    except HttpError as exc:
        logger.warning("Google Docs insert failed: %s", exc)
        raise ProposalGoogleDocExportError(
            _http_error_message(exc, fallback="Could not write proposal into Google Doc"),
            status_code=403 if exc.resp.status in (401, 403) else 502,
        ) from exc

    return {
        "documentId": document_id,
        "documentUrl": web_link,
        "title": doc_title,
        "sectionCount": len(sections),
    }


async def export_proposal_to_google_doc(
    *,
    rfp_title: str,
    draft: ProposalDraft,
) -> dict[str, Any]:
    doc_title = _sanitize_doc_title(f"{rfp_title} — Proposal")
    return await asyncio.to_thread(
        _export_sync,
        doc_title=doc_title,
        draft=draft,
        existing_document_id=draft.google_doc_id,
        existing_document_url=draft.google_doc_url,
    )
