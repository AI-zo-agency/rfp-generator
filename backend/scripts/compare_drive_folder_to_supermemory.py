#!/usr/bin/env python3
"""
Compare a Google Drive folder to Supermemory: what's ingested vs still pending.

Matches by Drive file ID (customId drive:{id} or metadata.driveFileId) — same as ingest script.

Usage:
  cd backend && source .venv/bin/activate

  python scripts/compare_drive_folder_to_supermemory.py \\
    --folder-id "1-Zfo5aJVrDiV3fAlwYoAv2KtQejuqH_q"

  python scripts/compare_drive_folder_to_supermemory.py \\
    --folder-name "6. RFP CLAUDE Specialis"

  python scripts/compare_drive_folder_to_supermemory.py \\
    --folder-id "1abc..." --json --verify-stubs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
_SCRIPTS_ROOT = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from app.core.config import settings
from app.services import supermemory

import ingest_drive_folder_to_supermemory as ingest


@dataclass
class SupermemoryIndex:
    by_drive_id: dict[str, dict[str, Any]]
    kb_total: int


async def build_supermemory_index() -> SupermemoryIndex:
    docs = await supermemory.list_all_container_documents(force_refresh=True)
    kb_docs = [doc for doc in docs if supermemory.is_knowledge_base_document(doc)]
    by_drive_id: dict[str, dict[str, Any]] = {}
    for doc in kb_docs:
        drive_id = supermemory.drive_file_id_from_document(doc)
        if drive_id:
            by_drive_id[drive_id] = doc
    return SupermemoryIndex(by_drive_id=by_drive_id, kb_total=len(kb_docs))


@dataclass
class FileStatus:
    drive_file: ingest.DriveFile
    category: str
    ingested: bool
    supermemory_id: str | None
    supermemory_status: str | None
    stale: bool = False
    stub: bool = False


async def compare_folder(
    folder_id: str,
    *,
    recursive: bool = True,
    verify_stubs: bool = False,
) -> dict[str, Any]:
    service = ingest._drive_service()
    drive_files = ingest.list_folder_files(
        service,
        folder_id,
        recursive=recursive,
    )
    sm_index = await build_supermemory_index()

    ingested: list[FileStatus] = []
    pending: list[FileStatus] = []
    stale: list[FileStatus] = []
    stub: list[FileStatus] = []

    for drive_file in drive_files:
        parsed = ingest.parse_filename(drive_file.name)
        doc = sm_index.by_drive_id.get(drive_file.id)
        is_stale = bool(
            doc
            and ingest._drive_is_newer_than_supermemory(drive_file, doc)
        )
        is_stub = False
        if verify_stubs and doc:
            indexed = await supermemory.get_document_content(
                custom_id=f"drive:{drive_file.id}"
            )
            is_stub = ingest.looks_like_ingest_stub(indexed)

        status = FileStatus(
            drive_file=drive_file,
            category=parsed.category,
            ingested=doc is not None,
            supermemory_id=str(doc.get("id")) if doc else None,
            supermemory_status=str(doc.get("status")) if doc else None,
            stale=is_stale,
            stub=is_stub,
        )
        if status.ingested:
            ingested.append(status)
            if is_stale:
                stale.append(status)
            if is_stub:
                stub.append(status)
        else:
            pending.append(status)

    ingested.sort(key=lambda row: row.drive_file.name.lower())
    pending.sort(key=lambda row: row.drive_file.name.lower())

    return {
        "folderId": folder_id,
        "containerTag": settings.resolved_container_tag,
        "recursive": recursive,
        "driveFileCount": len(drive_files),
        "supermemoryKbTotal": sm_index.kb_total,
        "ingestedCount": len(ingested),
        "pendingCount": len(pending),
        "staleCount": len(stale),
        "stubCount": len(stub),
        "ingested": [
            {
                "driveFileId": row.drive_file.id,
                "fileName": row.drive_file.name,
                "folderPath": row.drive_file.folder_path,
                "category": row.category,
                "supermemoryId": row.supermemory_id,
                "status": row.supermemory_status,
                "stale": row.stale,
                "stub": row.stub,
            }
            for row in ingested
        ],
        "pending": [
            {
                "driveFileId": row.drive_file.id,
                "fileName": row.drive_file.name,
                "folderPath": row.drive_file.folder_path,
                "category": row.category,
            }
            for row in pending
        ],
    }


def _print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 72)
    print("Drive folder vs Supermemory")
    print("=" * 72)
    print(f"Container:     {report['containerTag']}")
    print(f"Folder ID:     {report['folderId']}")
    print(f"Recursive:     {report['recursive']}")
    print(f"Drive files:   {report['driveFileCount']}")
    print(f"KB in memory:  {report['supermemoryKbTotal']}")
    print(f"Ingested:      {report['ingestedCount']}")
    print(f"Left to ingest:{report['pendingCount']}")
    if report.get("staleCount"):
        print(f"Stale on Drive:{report['staleCount']} (Drive newer than Supermemory)")
    if report.get("stubCount"):
        print(f"Stub content:  {report['stubCount']} (indexed as loading/empty)")
    print()

    if report["ingested"]:
        print(f"--- Already in Supermemory ({report['ingestedCount']}) ---")
        for row in report["ingested"]:
            flags = []
            if row.get("stale"):
                flags.append("stale")
            if row.get("stub"):
                flags.append("stub")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            path = row.get("folderPath") or ""
            prefix = f"{path}" if path else ""
            print(
                f"  ✓ [{row['category']}] {prefix}{row['fileName']}  "
                f"({row.get('status') or '?'}){suffix}"
            )
        print()

    if report["pending"]:
        print(f"--- Left to ingest ({report['pendingCount']}) ---")
        for row in report["pending"]:
            path = row.get("folderPath") or ""
            prefix = f"{path}" if path else ""
            print(f"  ○ [{row['category']}] {prefix}{row['fileName']}")
        print()

    if report["pending"] or report.get("staleCount") or report.get("stubCount"):
        print("Suggested commands:")
        if report["pending"]:
            print(
                f"  python scripts/ingest_drive_folder_to_supermemory.py "
                f"--folder-id \"{report['folderId']}\""
            )
        if report.get("stubCount"):
            print(
                f"  python scripts/ingest_drive_folder_to_supermemory.py "
                f"--folder-id \"{report['folderId']}\" --verify-stubs"
            )
        if report.get("staleCount"):
            print(
                f"  python scripts/ingest_drive_folder_to_supermemory.py "
                f"--folder-id \"{report['folderId']}\" --refresh-stale"
            )
    else:
        print("All Drive files in this folder are indexed in Supermemory.")
    print()


async def run(args: argparse.Namespace) -> int:
    if not supermemory.is_configured():
        raise SystemExit("SUPERMEMORY_API_KEY is not set in backend/.env")

    service = ingest._drive_service()
    folder_id = args.folder_id
    if not folder_id and args.folder_name:
        folder_id = ingest.find_folder_id_by_name(service, args.folder_name)
        if not folder_id:
            raise SystemExit(f"Folder not found: {args.folder_name!r}")
    if not folder_id:
        raise SystemExit("Provide --folder-id or --folder-name")

    report = await compare_folder(
        folder_id,
        recursive=not args.no_recursive,
        verify_stubs=args.verify_stubs,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show Drive folder files already in Supermemory vs pending ingest."
    )
    parser.add_argument("--folder-id", help="Google Drive folder ID")
    parser.add_argument("--folder-name", help="Search Drive for folder by name")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only compare files in the top folder (default: include subfolders)",
    )
    parser.add_argument(
        "--verify-stubs",
        action="store_true",
        help="Fetch indexed content and flag docs that look empty or like Drive loading pages",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
