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
    --folder-id "1abc..." --json
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


async def _list_all_container_documents(*, page_size: int = 100) -> list[dict[str, Any]]:
    """Paginate Supermemory /v3/documents/list for the zo-agency container."""
    import httpx

    if not supermemory.is_configured():
        return []

    url = f"{settings.supermemory_base_url.rstrip('/')}/v3/documents/list"
    headers = {"Authorization": f"Bearer {settings.supermemory_api_key}"}
    tag = settings.resolved_container_tag

    all_docs: list[dict[str, Any]] = []
    page = 1

    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            body = {"containerTag": tag, "limit": page_size, "page": page}
            response = await client.post(url, json=body, headers=headers)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Supermemory list failed ({response.status_code}): {response.text}"
                )
            data = response.json()
            batch = data.get("memories") or data.get("documents") or data.get("items") or []
            if not isinstance(batch, list):
                break
            all_docs.extend(item for item in batch if isinstance(item, dict))

            pagination = data.get("pagination") if isinstance(data, dict) else None
            if not isinstance(pagination, dict):
                break
            total_pages = int(pagination.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1

    return all_docs


def _is_knowledge_base_doc(doc: dict[str, Any]) -> bool:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_type = metadata.get("type") or doc.get("type")
    if doc_type == "knowledge_base":
        return True
    if str(doc.get("customId") or "").startswith("drive:"):
        return True
    return False


def _drive_id_from_doc(doc: dict[str, Any]) -> str | None:
    custom = str(doc.get("customId") or "")
    if custom.startswith("drive:"):
        return custom.removeprefix("drive:")
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    drive_id = metadata.get("driveFileId")
    return str(drive_id) if drive_id else None


async def build_supermemory_index() -> SupermemoryIndex:
    docs = await _list_all_container_documents()
    kb_docs = [doc for doc in docs if _is_knowledge_base_doc(doc)]
    by_drive_id: dict[str, dict[str, Any]] = {}
    for doc in kb_docs:
        drive_id = _drive_id_from_doc(doc)
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


async def compare_folder(folder_id: str) -> dict[str, Any]:
    service = ingest._drive_service()
    drive_files = ingest.list_folder_files(service, folder_id)
    sm_index = await build_supermemory_index()

    ingested: list[FileStatus] = []
    pending: list[FileStatus] = []

    for drive_file in drive_files:
        parsed = ingest.parse_filename(drive_file.name)
        doc = sm_index.by_drive_id.get(drive_file.id)
        status = FileStatus(
            drive_file=drive_file,
            category=parsed.category,
            ingested=doc is not None,
            supermemory_id=str(doc.get("id")) if doc else None,
            supermemory_status=str(doc.get("status")) if doc else None,
        )
        if status.ingested:
            ingested.append(status)
        else:
            pending.append(status)

    ingested.sort(key=lambda row: row.drive_file.name.lower())
    pending.sort(key=lambda row: row.drive_file.name.lower())

    return {
        "folderId": folder_id,
        "containerTag": settings.resolved_container_tag,
        "driveFileCount": len(drive_files),
        "supermemoryKbTotal": sm_index.kb_total,
        "ingestedCount": len(ingested),
        "pendingCount": len(pending),
        "ingested": [
            {
                "driveFileId": row.drive_file.id,
                "fileName": row.drive_file.name,
                "category": row.category,
                "supermemoryId": row.supermemory_id,
                "status": row.supermemory_status,
            }
            for row in ingested
        ],
        "pending": [
            {
                "driveFileId": row.drive_file.id,
                "fileName": row.drive_file.name,
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
    print(f"Drive files:   {report['driveFileCount']}")
    print(f"KB in memory:  {report['supermemoryKbTotal']} (all containers docs)")
    print(f"Ingested:      {report['ingestedCount']}")
    print(f"Left to ingest:{report['pendingCount']}")
    print()

    if report["ingested"]:
        print(f"--- Already in Supermemory ({report['ingestedCount']}) ---")
        for row in report["ingested"]:
            status = row.get("status") or "?"
            print(f"  ✓ [{row['category']}] {row['fileName']}  ({status})")
        print()

    if report["pending"]:
        print(f"--- Left to ingest ({report['pendingCount']}) ---")
        for row in report["pending"]:
            print(f"  ○ [{row['category']}] {row['fileName']}")
        print()
        print("Ingest pending files:")
        print(
            f"  python scripts/ingest_drive_folder_to_supermemory.py "
            f"--folder-id \"{report['folderId']}\""
        )
    else:
        print("All Drive files in this folder are already in Supermemory.")
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

    report = await compare_folder(folder_id)

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
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
