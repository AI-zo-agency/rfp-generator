#!/usr/bin/env python3
"""
Ingest every file in a Google Drive folder into Supermemory (zo-agency container).

ENHANCED: Downloads and uploads files directly to ensure proper content extraction.
This avoids Google Drive permission issues and ensures PDFs are properly indexed.

Modes:
  - upload (default, RECOMMENDED): Download + upload all files (most reliable, proper extraction)
  - batch: URL-based ingestion (faster but often fails with Drive permissions)

Setup (backend/.env):
  SUPERMEMORY_API_KEY=...
  SUPERMEMORY_CONTAINER_TAG=zo-agency
  GOOGLE_CLIENT_ID=....apps.googleusercontent.com
  GOOGLE_CLIENT_SECRET=...
  GOOGLE_REFRESH_TOKEN=...

Usage:
  cd backend && source .venv/bin/activate

  # RECOMMENDED: Upload mode (downloads and uploads files directly)
  python scripts/ingest_drive_folder_to_supermemory.py \
    --folder-id "1-Zfo5aJVrDiV3fAlwYoAv2KtQejuqH_q"

  # By folder name
  python scripts/ingest_drive_folder_to_supermemory.py \
    --folder-name "6. RFP CLAUDE Specialis"

  # Increase parallelism (if no errors)
  python scripts/ingest_drive_folder_to_supermemory.py \
    --folder-id "..." --workers 3

  # Dry run to see what would be ingested
  python scripts/ingest_drive_folder_to_supermemory.py --folder-id "..." --dry-run
  
Note: Files larger than 50MB will be skipped (Supermemory has file size limits)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.core.config import settings
from app.services.google_oauth import GoogleOAuthError, build_drive_service
from app.services.knowledge_base_document_types import category_title
from app.services import supermemory

logger = logging.getLogger("ingest_drive_folder")

FOLDER_MIME = "application/vnd.google-apps.folder"
BATCH_SIZE = 100

PREFIX_TO_CATEGORY: dict[str, str] = {
    "00": "reference",
    "01": "verified_facts",
    "02": "reference",
    "03": "case_study",
    "04": "team_bio",
    "05": "pricing",
    "06": "won_proposal",
    "07": "finalist_proposal",
    "08": "lost_proposal",
    "09": "scoring_debrief",
    "10": "active_rfp",
    "11": "reference",
}

CODE_TO_CATEGORY: dict[str, str] = {
    "CS": "case_study",
    "WON": "won_proposal",
    "FIN": "finalist_proposal",
    "LOST": "lost_proposal",
    "REF": "reference",
    "BIO": "team_bio",
    "PRICE": "pricing",
    "RFP": "active_rfp",
}

SUPPORTED_EXPORTS: dict[str, str] = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "application/vnd.google-apps.presentation": "application/pdf",
}

# When Google Docs PDF export hits size limits, try smaller export formats.
GOOGLE_DOC_EXPORT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("application/pdf", ".pdf"),
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    ("text/plain", ".txt"),
)

GOOGLE_SLIDES_EXPORT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("application/pdf", ".pdf"),
    ("text/plain", ".txt"),
    (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
)

SKIP_MIMES = {FOLDER_MIME, "application/vnd.google-apps.shortcut"}

PRICING_HINTS = ("pricing", "price", "rate")

# PDF mime types that need upload mode (not batch URL mode)
PDF_MIMES = {
    "application/pdf",
}

# File size limits (bytes)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB - Supermemory likely has limits around here
WARN_FILE_SIZE = 20 * 1024 * 1024  # 20 MB - warn for large files


@dataclass
class ParsedFilename:
    category: str
    category_title: str
    title: str
    client: str | None
    doc_kind: str | None
    year: str | None
    prefix: str | None
    code: str | None


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str | None
    web_view_link: str | None


def _extract_year(text: str) -> str | None:
    for token in text.replace("-", "_").split("_"):
        if len(token) == 4 and token.isdigit() and token.startswith("20"):
            return token
    return None


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip()


def _split_body(body: str) -> tuple[str | None, str | None]:
    parts = [part for part in body.split("_") if part]
    if not parts:
        return None, None
    if parts[-1].isdigit() and len(parts[-1]) == 4:
        parts = parts[:-1]
    if len(parts) == 1:
        return None, parts[0]
    client = parts[0]
    doc_kind = "_".join(parts[1:]) if len(parts) > 1 else None
    if client and client[0].islower():
        return None, "_".join(parts)
    return client, doc_kind


def _build_title(client: str | None, doc_kind: str | None, body: str) -> str:
    if client and doc_kind:
        return f"{_humanize(client)} — {_humanize(doc_kind)}"
    return _humanize(body)


def parse_filename(filename: str) -> ParsedFilename:
    """Derive Supermemory metadata from a ZO-style Drive filename (no regex)."""
    stem = Path(filename).stem
    lower = stem.lower()

    if lower.startswith("zo_"):
        title = stem.replace("_", " ")
        return ParsedFilename(
            category="reference",
            category_title=category_title("reference"),
            title=title,
            client=None,
            doc_kind="guide",
            year=_extract_year(stem),
            prefix="zo",
            code=None,
        )

    parts = stem.split("_")
    prefix: str | None = None
    code: str | None = None
    body_parts = parts

    if parts and len(parts[0]) == 2 and parts[0].isdigit():
        prefix = parts[0]
        body_parts = parts[1:]
        if (
            body_parts
            and body_parts[0].isalpha()
            and body_parts[0].upper() == body_parts[0]
            and len(body_parts[0]) <= 6
        ):
            code = body_parts[0].upper()
            body_parts = body_parts[1:]

    body = "_".join(body_parts)
    category = CODE_TO_CATEGORY.get(code or "") or PREFIX_TO_CATEGORY.get(prefix or "", "reference")
    body_lower = body.lower()
    if prefix == "00" and any(hint in body_lower for hint in PRICING_HINTS):
        category = "pricing"

    client, doc_kind = _split_body(body)
    title = _build_title(client, doc_kind, body)

    return ParsedFilename(
        category=category,
        category_title=category_title(category),
        title=title,
        client=client,
        doc_kind=doc_kind,
        year=_extract_year(body),
        prefix=prefix,
        code=code,
    )


def _drive_service():
    try:
        return build_drive_service()
    except GoogleOAuthError as exc:
        raise SystemExit(str(exc)) from exc


def find_folder_id_by_name(service: Any, folder_name: str) -> str | None:
    escaped = folder_name.replace("'", "\\'")
    query = (
        f"name = '{escaped}' and "
        f"mimeType = '{FOLDER_MIME}' and trashed = false"
    )
    response = (
        service.files()
        .list(
            q=query,
            pageSize=10,
            fields="files(id, name, parents)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        .execute()
    )
    files = response.get("files", [])
    if not files:
        return None
    if len(files) > 1:
        logger.warning(
            "Multiple folders named %r — using first match: %s",
            folder_name,
            files[0]["id"],
        )
    return files[0]["id"]


def list_folder_files(service: Any, folder_id: str) -> list[DriveFile]:
    query = f"'{folder_id}' in parents and trashed = false"
    files: list[DriveFile] = []
    page_token: str | None = None

    while True:
        response = (
            service.files()
            .list(
                q=query,
                pageSize=100,
                pageToken=page_token,
                orderBy="name_natural",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields=(
                    "nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)"
                ),
            )
            .execute()
        )

        for item in response.get("files", []):
            mime = item.get("mimeType", "")
            if mime in SKIP_MIMES:
                continue
            files.append(
                DriveFile(
                    id=item["id"],
                    name=item.get("name", "untitled"),
                    mime_type=mime,
                    modified_time=item.get("modifiedTime"),
                    web_view_link=item.get("webViewLink"),
                )
            )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return files


def drive_content_url(drive_file: DriveFile) -> str:
    """URL Supermemory batch ingest fetches — export link for Google native types."""
    if drive_file.mime_type == "application/vnd.google-apps.document":
        return f"https://docs.google.com/document/d/{drive_file.id}/export?format=pdf"
    if drive_file.mime_type == "application/vnd.google-apps.spreadsheet":
        return (
            f"https://docs.google.com/spreadsheets/d/{drive_file.id}/export"
            "?format=xlsx"
        )
    if drive_file.mime_type == "application/vnd.google-apps.presentation":
        return f"https://docs.google.com/presentation/d/{drive_file.id}/export/pdf"
    if drive_file.web_view_link:
        return drive_file.web_view_link
    return f"https://drive.google.com/file/d/{drive_file.id}/view"


def build_metadata(
    drive_file: DriveFile,
    parsed: ParsedFilename,
    *,
    folder_id: str,
) -> dict[str, str | int | bool]:
    metadata: dict[str, str | int | bool] = {
        "type": "knowledge_base",
        "title": parsed.title,
        "category": parsed.category,
        "categoryTitle": parsed.category_title,
        "fileName": drive_file.name,
        "source": "google-drive",
        "driveFileId": drive_file.id,
        "sourceFolderId": folder_id,
    }
    if parsed.client:
        metadata["client"] = parsed.client
    if parsed.doc_kind:
        metadata["docKind"] = parsed.doc_kind
    if parsed.year:
        metadata["year"] = parsed.year
    if parsed.prefix:
        metadata["filingPrefix"] = parsed.prefix
    if parsed.code:
        metadata["filingCode"] = parsed.code
    if drive_file.web_view_link:
        metadata["driveUrl"] = drive_file.web_view_link
    return metadata


def _export_media_bytes(
    service: Any,
    file_id: str,
    export_mime: str,
) -> bytes:
    request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def download_file_bytes(service: Any, drive_file: DriveFile) -> tuple[bytes, str]:
    stem = Path(drive_file.name).stem

    if drive_file.mime_type == "application/vnd.google-apps.document":
        return _download_with_export_fallbacks(
            service,
            drive_file,
            GOOGLE_DOC_EXPORT_FALLBACKS,
            stem,
        )

    if drive_file.mime_type == "application/vnd.google-apps.presentation":
        return _download_with_export_fallbacks(
            service,
            drive_file,
            GOOGLE_SLIDES_EXPORT_FALLBACKS,
            stem,
        )

    export_mime = SUPPORTED_EXPORTS.get(drive_file.mime_type)

    if export_mime:
        ext = ".pdf" if export_mime == "application/pdf" else ".xlsx"
        upload_name = f"{stem}{ext}"
        return _export_media_bytes(service, drive_file.id, export_mime), upload_name

    request = service.files().get_media(fileId=drive_file.id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue(), drive_file.name


def _download_with_export_fallbacks(
    service: Any,
    drive_file: DriveFile,
    fallbacks: tuple[tuple[str, str], ...],
    stem: str,
) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for export_mime, ext in fallbacks:
        upload_name = f"{stem}{ext}"
        try:
            logger.info("Exporting %s as %s", drive_file.name, export_mime)
            file_bytes = _export_media_bytes(service, drive_file.id, export_mime)
            if not file_bytes:
                raise RuntimeError(f"Empty export for {drive_file.name}")
            logger.info(
                "Exported %s → %s (%d bytes)",
                drive_file.name,
                upload_name,
                len(file_bytes),
            )
            return file_bytes, upload_name
        except HttpError as exc:
            last_error = exc
            logger.warning(
                "Export %s failed for %s: %s",
                export_mime,
                drive_file.name,
                exc,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Export %s failed for %s: %s",
                export_mime,
                drive_file.name,
                exc,
            )
    raise RuntimeError(f"All export formats failed for {drive_file.name}") from last_error


async def ingest_batch_urls(
    drive_files: list[DriveFile],
    *,
    folder_id: str,
    dry_run: bool,
    batch_size: int,
) -> tuple[int, int]:
    """One or few batch API calls — Supermemory fetches each Drive URL."""
    ok = 0
    failed = 0
    documents: list[dict[str, Any]] = []

    for drive_file in drive_files:
        parsed = parse_filename(drive_file.name)
        documents.append(
            {
                "customId": f"drive:{drive_file.id}",
                "content": drive_content_url(drive_file),
                "metadata": build_metadata(drive_file, parsed, folder_id=folder_id),
            }
        )

    if dry_run:
        for doc in documents:
            logger.info(
                "dry-run batch: %s → %s | %s",
                doc["metadata"]["fileName"],
                doc["metadata"]["category"],
                doc["content"][:80],
            )
        return len(documents), 0

    for start in range(0, len(documents), batch_size):
        chunk = documents[start : start + batch_size]
        batch_num = start // batch_size + 1
        logger.info(
            "Batch %d: submitting %d document URL(s) to Supermemory",
            batch_num,
            len(chunk),
        )
        try:
            result = await supermemory.batch_add_documents(chunk, dreaming="instant")
            results = result.get("results") or []
            for item in results:
                if not isinstance(item, dict):
                    continue
                custom_id = item.get("customId") or item.get("id")
                status = str(item.get("status") or "unknown")
                file_name = next(
                    (
                        doc["metadata"]["fileName"]
                        for doc in chunk
                        if doc.get("customId") == custom_id
                    ),
                    custom_id,
                )
                if status == "error":
                    failed += 1
                    logger.error("  ERROR: %s customId=%s", file_name, custom_id)
                else:
                    ok += 1
                    logger.info("  %s: %s customId=%s", status, file_name, custom_id)
            if not results:
                ok += len(chunk)
        except Exception as exc:
            failed += len(chunk)
            logger.error("Batch %d failed: %s", batch_num, exc)

    return ok, failed


async def ingest_parallel_uploads(
    drive_files: list[DriveFile],
    *,
    folder_id: str,
    dry_run: bool,
    workers: int,
) -> tuple[int, int]:
    """
    Download files + parallel upload to Supermemory.
    Supermemory handles text extraction (including OCR) on their end.
    """
    semaphore = asyncio.Semaphore(max(1, workers))
    
    # Create a new Drive service for each batch to avoid memory issues
    def get_fresh_service():
        return _drive_service()

    async def one_file(drive_file: DriveFile, retry_count: int = 3) -> bool:
        parsed = parse_filename(drive_file.name)
        custom_id = f"drive:{drive_file.id}"
        metadata = build_metadata(drive_file, parsed, folder_id=folder_id)

        if dry_run:
            logger.info("dry-run upload: %s → %s", drive_file.name, parsed.category)
            return True

        async with semaphore:
            for attempt in range(1, retry_count + 1):
                try:
                    # Use fresh service to avoid memory corruption
                    service = get_fresh_service()
                    
                    file_bytes, upload_name = await asyncio.to_thread(
                        download_file_bytes,
                        service,
                        drive_file,
                    )
                    
                    file_size = len(file_bytes)
                    
                    # Check file size limits
                    if file_size > MAX_FILE_SIZE:
                        logger.error(
                            "❌ SKIPPING %s: File too large (%d MB > %d MB limit). "
                            "Supermemory may not support files this large.",
                            upload_name, file_size // (1024*1024), MAX_FILE_SIZE // (1024*1024)
                        )
                        logger.info("   💡 TIP: Try compressing this PDF or splitting it into smaller files")
                        del file_bytes
                        del service
                        return False
                    
                    if file_size > WARN_FILE_SIZE:
                        logger.warning(
                            "⚠️  %s is large (%d MB) - upload may take a while or fail",
                            upload_name, file_size // (1024*1024)
                        )
                    
                    logger.info("⬆️  Uploading %s (%d MB) [attempt %d/%d]", 
                               upload_name, file_size // (1024*1024), attempt, retry_count)
                    
                    await supermemory.upload_file_document(
                        file_bytes=file_bytes,
                        filename=upload_name,
                        custom_id=custom_id,
                        metadata=metadata,
                    )
                    
                    logger.info("✅ Uploaded %s", upload_name)
                    
                    # Clean up to free memory
                    del file_bytes
                    del service
                    
                    # No delay - upload as fast as possible
                    
                    return True
                    
                except Exception as exc:
                    error_msg = str(exc)
                    
                    # Check if it's a retryable error
                    is_retryable = any(code in error_msg for code in ["503", "502", "504", "429"])
                    
                    # Don't retry if it's likely a file size issue
                    if "503" in error_msg and "file_size" in locals() and file_size > WARN_FILE_SIZE:
                        logger.error(
                            "❌ %s failed with 503 - likely TOO LARGE (%d MB). "
                            "Consider compressing or splitting this file.",
                            drive_file.name, file_size // (1024*1024)
                        )
                        return False
                    
                    if attempt < retry_count and is_retryable:
                        wait_time = 1  # Faster retry - just 1 second
                        logger.warning(
                            "⚠️  %s failed (attempt %d/%d): %s - retrying in %ds...", 
                            drive_file.name, attempt, retry_count, error_msg, wait_time
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error("❌ Failed to upload %s after %d attempts: %s", 
                                   drive_file.name, attempt, error_msg)
                        return False
            
            return False

    results = await asyncio.gather(
        *[one_file(df) for df in drive_files],
        return_exceptions=True,
    )
    
    ok = sum(1 for r in results if r is True)
    failed = len(results) - ok
    
    for drive_file, result in zip(drive_files, results):
        if isinstance(result, Exception):
            logger.error("❌ EXCEPTION for %s: %s", drive_file.name, result)
    
    return ok, failed


async def run(args: argparse.Namespace) -> int:
    if not supermemory.is_configured():
        raise SystemExit("SUPERMEMORY_API_KEY is not set in backend/.env")

    if args.drive_import:
        if args.dry_run:
            logger.info("dry-run: would call Supermemory Google Drive import")
            return 0
        logger.info("Triggering Supermemory Google Drive import for %s", settings.resolved_container_tag)
        result = await supermemory.trigger_google_drive_import()
        logger.info("Drive import accepted: %s", result)
        return 0

    service = _drive_service()

    folder_id = args.folder_id
    if not folder_id and args.folder_name:
        folder_id = find_folder_id_by_name(service, args.folder_name)
        if not folder_id:
            raise SystemExit(f"Folder not found: {args.folder_name!r}")

    if not folder_id:
        raise SystemExit("Provide --folder-id or --folder-name (or --drive-import)")

    drive_files = list_folder_files(service, folder_id)

    if args.drive_ids:
        wanted = {part.strip() for part in args.drive_ids.split(",") if part.strip()}
        drive_files = [f for f in drive_files if f.id in wanted]
        if not drive_files:
            raise SystemExit(f"No folder files matched --drive-ids: {', '.join(sorted(wanted))}")
        if args.mode == "batch":
            logger.info("--drive-ids set: using upload mode (batch URL failed for these files)")
            args.mode = "upload"

    if args.limit:
        drive_files = drive_files[: args.limit]

    if not drive_files:
        logger.info("No ingestible files in folder %s", folder_id)
        return 0

    # Check what's already uploaded to avoid duplicates
    logger.info("Checking existing documents in Supermemory...")
    existing_docs = await supermemory.list_container_memories(limit=1000)
    existing_drive_ids = {
        doc.get("customId", "").replace("drive:", "")
        for doc in existing_docs
        if doc.get("customId", "").startswith("drive:")
    }
    
    # Filter out already uploaded files
    files_to_upload = [f for f in drive_files if f.id not in existing_drive_ids]
    skipped = len(drive_files) - len(files_to_upload)
    
    logger.info(
        "Container: %s | Folder: %s | Total files: %d | mode=%s | dry_run=%s",
        settings.resolved_container_tag,
        folder_id,
        len(drive_files),
        args.mode,
        args.dry_run,
    )
    
    if skipped > 0:
        logger.info("⏭️  Skipping %d already uploaded files", skipped)
    
    logger.info("📝 To upload: %d files", len(files_to_upload))
    
    if not files_to_upload:
        logger.info("✅ All files already uploaded!")
        return 0
    
    # Separate large files that will be skipped
    large_files = [f for f in files_to_upload if f.mime_type in PDF_MIMES]  # Will check size during upload
    
    ok = 0
    failed = 0

    # Upload mode: download + upload all files (most reliable)
    if args.mode == "upload":
        logger.info("\n=== Processing %d files with upload mode ===", len(files_to_upload))
        logger.info("📦 This ensures proper content extraction and avoids Drive permission issues")
        try:
            ok, failed = await ingest_parallel_uploads(
                files_to_upload,
                folder_id=folder_id,
                dry_run=args.dry_run,
                workers=args.workers,
            )
            logger.info("Upload batch complete: %d success, %d failed", ok, failed)
        except Exception as exc:
            logger.error("Upload batch crashed: %s", exc)
            logger.error("Progress saved - some files may have uploaded before crash")
    
    # Batch mode: URL-based (faster but often fails)
    else:
        logger.info("\n=== Processing %d files with batch mode ===", len(files_to_upload))
        logger.warning("⚠️  Batch mode often fails due to Drive permissions. Use --mode upload for reliability.")
        try:
            ok, failed = await ingest_batch_urls(
                files_to_upload,
                folder_id=folder_id,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
            )
            logger.info("Batch upload complete: %d success, %d failed", ok, failed)
            
            if failed > 0:
                logger.warning("💡 TIP: Retry failed files with: --mode upload")
        except Exception as exc:
            logger.error("Batch upload crashed: %s", exc)

    logger.info("\n" + "="*60)
    logger.info("✅ DONE: success=%d failed=%d", ok, failed)
    
    total_expected = len([f for f in drive_files if f.id not in existing_drive_ids]) if 'existing_drive_ids' in locals() else len(drive_files)
    if ok + failed < total_expected:
        logger.warning("⚠️  Script may have crashed before completing all uploads")
        logger.warning("   Uploaded: %d, Failed: %d, Expected: %d", ok, failed, total_expected)
        logger.warning("   Run the script again - it will skip already uploaded files")
    elif failed > 0:
        logger.warning("⚠️  Some files failed - check logs above for details")
    else:
        logger.info("🎉 All files successfully ingested!")
    
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a Google Drive folder into Supermemory with smart PDF handling."
    )
    parser.add_argument("--folder-id", help="Google Drive folder ID")
    parser.add_argument("--folder-name", help='Folder name, e.g. "6. RFP CLAUDE Specialis"')
    parser.add_argument(
        "--drive-ids",
        help="Comma-separated Drive file IDs to ingest only (e.g. id1,id2,id3)",
    )
    parser.add_argument(
        "--mode",
        choices=("upload", "batch"),
        default="upload",
        help=(
            "upload = download + upload all files (RECOMMENDED, most reliable); "
            "batch = URL batch API (faster but often fails with Drive permissions)"
        ),
    )
    parser.add_argument(
        "--drive-import",
        action="store_true",
        help="Use Supermemory native Google Drive import (requires SM Drive connection)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and log only")
    parser.add_argument("--limit", type=int, default=0, help="Max files (0 = all)")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Documents per batch request (max 600, default {BATCH_SIZE})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Parallel uploads when using upload mode (default: 10 for speed)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
