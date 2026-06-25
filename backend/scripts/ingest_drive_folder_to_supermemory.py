#!/usr/bin/env python3
"""
Ingest every file in a Google Drive folder into Supermemory (zo-agency container).

Files are downloaded in memory only — nothing is written to localhost.

Filename → category mapping (ZO filing prefixes):
  00_  Guides & System        → reference
  01_  Verified Facts        → verified_facts
  02_  Master Template       → reference
  03_CS_ Case Studies         → case_study
  04_  Team Bios             → team_bio
  05_  Pricing               → pricing
  06_WON_ Won Proposals       → won_proposal
  07_FIN_ Finalist Proposals  → finalist_proposal
  08_  Lost + FOIA           → lost_proposal
  09_  Scoring & Debriefs    → scoring_debrief
  10_  Active RFPs           → active_rfp
  11_REF_ Reference Archive   → reference
  zo_*  Agency guides         → reference

Setup (backend/.env):
  SUPERMEMORY_API_KEY=...
  SUPERMEMORY_CONTAINER_TAG=zo-agency
  GOOGLE_CLIENT_ID=....apps.googleusercontent.com
  GOOGLE_CLIENT_SECRET=...
  GOOGLE_REFRESH_TOKEN=...        # from scripts/google_oauth_setup.py (one-time)

Your Google account (OAuth) must have access to the folder.

Usage:
  cd backend
  source .venv/bin/activate

  # By folder ID (from Drive URL: .../folders/FOLDER_ID)
  python scripts/ingest_drive_folder_to_supermemory.py \\
    --folder-id "1abc...xyz"

  # By folder name (searches My Drive + all shared drives)
  python scripts/ingest_drive_folder_to_supermemory.py \\
    --folder-name "6. RFP CLAUDE Specialis"

  # Preview only — no uploads
  python scripts/ingest_drive_folder_to_supermemory.py \\
    --folder-id "1-Zfo5aJVrDiV3fAlwYoAv2KtQejuqH_q" --dry-run

  # Limit batch size
  python scripts/ingest_drive_folder_to_supermemory.py \\
    --folder-id "1abc...xyz" --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow `python scripts/ingest_...py` from backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.core.config import settings
from app.services.google_oauth import GoogleOAuthError, build_drive_service
from app.services.knowledge_base_document_types import category_title
from app.services import supermemory

logger = logging.getLogger("ingest_drive_folder")

FOLDER_MIME = "application/vnd.google-apps.folder"

# Numeric prefix → knowledge-base category (matches knowledge_base_document_types / ZO filing guide)
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

# Optional code token overrides (e.g. 03_CS_, 06_WON_, 07_FIN_, 11_REF_)
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

STANDARD_NAME = re.compile(
    r"^(?P<prefix>\d{2})_(?:(?P<code>[A-Z]+)_)?(?P<body>.+?)(?:\.[^.]+)?$",
    re.IGNORECASE,
)

YEAR_IN_NAME = re.compile(r"(20\d{2})")

SUPPORTED_EXPORTS: dict[str, str] = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "application/vnd.google-apps.presentation": "application/pdf",
}

SKIP_MIMES = {FOLDER_MIME, "application/vnd.google-apps.shortcut"}


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


def parse_filename(filename: str) -> ParsedFilename:
    """Derive Supermemory metadata from a ZO-style Drive filename."""
    stem = Path(filename).stem
    lower = stem.lower()

    if lower.startswith("zo_"):
        category = "reference"
        title = stem.replace("_", " ")
        return ParsedFilename(
            category=category,
            category_title=category_title(category),
            title=title,
            client=None,
            doc_kind="guide",
            year=_extract_year(stem),
            prefix="zo",
            code=None,
        )

    match = STANDARD_NAME.match(stem)
    if not match:
        category = "reference"
        return ParsedFilename(
            category=category,
            category_title=category_title(category),
            title=stem.replace("_", " "),
            client=None,
            doc_kind=None,
            year=_extract_year(stem),
            prefix=None,
            code=None,
        )

    prefix = match.group("prefix")
    code = (match.group("code") or "").upper()
    body = match.group("body")

    category = CODE_TO_CATEGORY.get(code) or PREFIX_TO_CATEGORY.get(prefix, "reference")
    # 00_Guide_Pricing etc. — pricing rules, not generic reference
    if prefix == "00" and re.search(r"pricing|price|rate", body, re.I):
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
        code=code or None,
    )


def _extract_year(text: str) -> str | None:
    match = YEAR_IN_NAME.search(text)
    return match.group(1) if match else None


def _split_body(body: str) -> tuple[str | None, str | None]:
    """
  Examples:
    CityofSanLeandro_winner_2026 → client, winner
    MaricopaCounty_Proposal_2025 → client, Proposal
    CaseStudyMaster_2025 → None, CaseStudyMaster
    """
    parts = [part for part in body.split("_") if part]
    if not parts:
        return None, None

    if parts[-1].isdigit() and len(parts[-1]) == 4:
        parts = parts[:-1]

    if len(parts) == 1:
        return None, parts[0]

    # Heuristic: first token is client (CityofX), rest is doc kind
    client = parts[0]
    doc_kind = "_".join(parts[1:]) if len(parts) > 1 else None
    if client and client[0].islower():
        client = None
        doc_kind = "_".join(parts)
    return client, doc_kind


def _build_title(client: str | None, doc_kind: str | None, body: str) -> str:
    if client and doc_kind:
        return f"{_humanize(client)} — {_humanize(doc_kind)}"
    return _humanize(body)


def _humanize(value: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return spaced.replace("_", " ").strip()


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


def download_file_bytes(service: Any, drive_file: DriveFile) -> tuple[bytes, str]:
    """Download file into memory. Returns (bytes, upload_filename)."""
    export_mime = SUPPORTED_EXPORTS.get(drive_file.mime_type)

    if export_mime:
        request = service.files().export_media(
            fileId=drive_file.id,
            mimeType=export_mime,
        )
        ext = ".pdf" if export_mime == "application/pdf" else ".xlsx"
        upload_name = f"{Path(drive_file.name).stem}{ext}"
    else:
        request = service.files().get_media(fileId=drive_file.id)
        upload_name = drive_file.name

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue(), upload_name


async def ingest_file(
    drive_file: DriveFile,
    parsed: ParsedFilename,
    *,
    folder_id: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    custom_id = f"drive:{drive_file.id}"

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

    if dry_run:
        return {
            "dryRun": True,
            "customId": custom_id,
            "fileName": drive_file.name,
            "category": parsed.category,
            "title": parsed.title,
        }

    service = _drive_service()
    file_bytes, upload_name = download_file_bytes(service, drive_file)

    return await supermemory.upload_file_document(
        file_bytes=file_bytes,
        filename=upload_name,
        custom_id=custom_id,
        metadata=metadata,
    )


async def run(args: argparse.Namespace) -> int:
    if not supermemory.is_configured():
        raise SystemExit("SUPERMEMORY_API_KEY is not set in backend/.env")

    service = _drive_service()

    folder_id = args.folder_id
    if not folder_id and args.folder_name:
        folder_id = find_folder_id_by_name(service, args.folder_name)
        if not folder_id:
            raise SystemExit(f"Folder not found: {args.folder_name!r}")

    if not folder_id:
        raise SystemExit("Provide --folder-id or --folder-name")

    drive_files = list_folder_files(service, folder_id)
    if args.limit:
        drive_files = drive_files[: args.limit]

    if not drive_files:
        logger.info("No ingestible files in folder %s", folder_id)
        return 0

    logger.info(
        "Container tag: %s | Folder: %s | Files: %d | dry_run=%s",
        settings.resolved_container_tag,
        folder_id,
        len(drive_files),
        args.dry_run,
    )

    ok = 0
    failed = 0

    for index, drive_file in enumerate(drive_files, start=1):
        parsed = parse_filename(drive_file.name)
        logger.info(
            "[%d/%d] %s → %s (%s)",
            index,
            len(drive_files),
            drive_file.name,
            parsed.category,
            parsed.title,
        )

        try:
            result = await ingest_file(
                drive_file,
                parsed,
                folder_id=folder_id,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                logger.info("  dry-run: %s", result)
            else:
                logger.info(
                    "  uploaded: id=%s status=%s",
                    (result or {}).get("id"),
                    (result or {}).get("status"),
                )
            ok += 1
        except Exception as exc:
            failed += 1
            logger.error("  FAILED %s: %s", drive_file.name, exc)

    logger.info("Done. success=%d failed=%d", ok, failed)
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a Google Drive folder into Supermemory (zo-agency)."
    )
    parser.add_argument(
        "--folder-id",
        help="Google Drive folder ID (from the folder URL)",
    )
    parser.add_argument(
        "--folder-name",
        help='Folder name to search, e.g. "6. RFP CLAUDE Specialis"',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log categorization only — no downloads or uploads",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of files to process (0 = all)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
