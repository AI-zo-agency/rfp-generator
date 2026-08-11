#!/usr/bin/env python3
"""
Copy documents from one Supermemory API key/container into another.

Use when the Drive source folder is gone but an older Supermemory account still
has indexed KB content.

Setup (backend/.env):
  SUPERMEMORY_API_KEY=...              # destination (active key)
  SUPERMEMORY_CONTAINER_TAG=zo-agency
  SUPERMEMORY_SOURCE_API_KEY=...       # source (old key with the docs)

Usage:
  cd backend && source .venv/bin/activate

  # Preview
  python scripts/migrate_supermemory_container.py --dry-run

  # Test with one doc
  python scripts/migrate_supermemory_container.py --limit 1

  # Full migration
  python scripts/migrate_supermemory_container.py

  # Re-copy everything (overwrite same customIds)
  python scripts/migrate_supermemory_container.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import httpx

from app.core.config import settings
from app.services import supermemory

logger = logging.getLogger("migrate_supermemory")

MIN_CONTENT_CHARS = 120
URL_ONLY_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

# Source RFP solicitation PDFs — not agency work, no value in KB
_RFP_SOURCE_RE = re.compile(
    r"(?:_rfp_|_rfp\.pdf$|(?:^|[_-])rfp(?:[_-]|\.pdf$))", re.IGNORECASE
)


def _is_source_rfp(doc: dict[str, Any]) -> bool:
    """True for client solicitation RFP PDFs (not proposals we wrote)."""
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    name = str(
        metadata.get("fileName") or doc.get("title") or doc.get("customId") or ""
    ).strip()
    n = name.casefold()
    if "proposal" in n:
        return False
    return bool(_RFP_SOURCE_RE.search(n))


def resolve_source_api_key(cli_key: str | None) -> str:
    key = (
        cli_key
        or os.environ.get("SUPERMEMORY_SOURCE_API_KEY")
        or settings.supermemory_source_api_key
        or ""
    ).strip()
    if not key:
        raise SystemExit(
            "Provide source key via --source-key or SUPERMEMORY_SOURCE_API_KEY in backend/.env"
        )
    return key


def _base_url() -> str:
    return settings.supermemory_base_url.rstrip("/")


def _headers(api_key: str, *, json_request: bool = True) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_request:
        headers["Content-Type"] = "application/json"
    return headers


async def list_all_documents(
    api_key: str,
    *,
    container_tag: str,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    url = f"{_base_url()}/v3/documents/list"
    all_docs: list[dict[str, Any]] = []
    page = 1

    async with httpx.AsyncClient(timeout=120.0) as client:
        while True:
            body = {"containerTag": container_tag, "limit": page_size, "page": page}
            response = await client.post(url, json=body, headers=_headers(api_key))
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Source list failed ({response.status_code}): {response.text[:300]}"
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


async def fetch_source_document(api_key: str, custom_id: str) -> dict[str, Any]:
    url = f"{_base_url()}/v3/documents/{custom_id}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(url, headers=_headers(api_key, json_request=False))
        if response.status_code >= 400:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}


def _doc_label(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    return str(metadata.get("fileName") or doc.get("title") or doc.get("customId") or "document")


def _metadata_for_upload(doc: dict[str, Any]) -> dict[str, str | int | bool]:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    clean: dict[str, str | int | bool] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, bool)):
            clean[key] = value
    if "type" not in clean:
        clean["type"] = "knowledge_base"
    return clean


def _content_ready(content: str) -> bool:
    text = (content or "").strip()
    if len(text) < MIN_CONTENT_CHARS:
        return False
    if URL_ONLY_RE.match(text):
        return False
    return True


async def migrate_one(
    *,
    source_key: str,
    source_doc: dict[str, Any],
    dry_run: bool,
) -> tuple[bool, str]:
    custom_id = str(source_doc.get("customId") or "").strip()
    if not custom_id:
        return False, "missing customId"

    label = _doc_label(source_doc)
    if dry_run:
        return True, f"would migrate {label}"

    record = await fetch_source_document(source_key, custom_id)
    content = str(record.get("content") or record.get("text") or "").strip()
    status = str(record.get("status") or source_doc.get("status") or "unknown")

    if not _content_ready(content):
        return False, f"no usable content (status={status}, len={len(content)})"

    metadata = _metadata_for_upload(source_doc)
    await supermemory.add_text_document(
        content=content,
        custom_id=custom_id,
        metadata=metadata,
    )
    return True, f"copied {len(content)} chars"


async def run(args: argparse.Namespace) -> int:
    if not supermemory.is_configured():
        raise SystemExit("SUPERMEMORY_API_KEY (destination) is not set in backend/.env")

    source_key = resolve_source_api_key(args.source_key)
    container = args.container_tag or settings.resolved_container_tag

    logger.info("Source container: %s", container)
    logger.info("Destination: active SUPERMEMORY_API_KEY")

    source_docs = await list_all_documents(source_key, container_tag=container)
    all_kb = [d for d in source_docs if supermemory.is_knowledge_base_document(d)]
    rfp_skipped = [d for d in all_kb if _is_source_rfp(d)]
    kb_docs = [d for d in all_kb if not _is_source_rfp(d)]
    if rfp_skipped:
        logger.info(
            "Skipping %d source RFP files (not agency work):", len(rfp_skipped)
        )
        for d in rfp_skipped:
            logger.info("  ⏭️  %s", _doc_label(d))
    if args.limit:
        kb_docs = kb_docs[: args.limit]

    dest_docs = await supermemory.list_all_container_documents(force_refresh=True)
    dest_custom_ids = {
        str(d.get("customId") or "").strip()
        for d in dest_docs
        if str(d.get("customId") or "").strip()
    }

    to_copy = kb_docs
    if not args.force:
        to_copy = [d for d in kb_docs if str(d.get("customId") or "").strip() not in dest_custom_ids]

    logger.info(
        "Source KB docs: %d | To copy: %d | Already on dest: %d | dry_run=%s",
        len(kb_docs),
        len(to_copy),
        len(kb_docs) - len(to_copy),
        args.dry_run,
    )

    if not to_copy:
        logger.info("Nothing to migrate.")
        return 0

    ok = 0
    failed = 0
    for index, doc in enumerate(to_copy, start=1):
        label = _doc_label(doc)
        try:
            success, message = await migrate_one(
                source_key=source_key,
                source_doc=doc,
                dry_run=args.dry_run,
            )
            if success:
                ok += 1
                logger.info("[%d/%d] ✅ %s — %s", index, len(to_copy), label, message)
            else:
                failed += 1
                logger.warning("[%d/%d] ⚠️  %s — %s", index, len(to_copy), label, message)
        except Exception as exc:
            failed += 1
            logger.error("[%d/%d] ❌ %s — %s", index, len(to_copy), label, exc)

        if not args.dry_run and index < len(to_copy):
            await asyncio.sleep(args.delay)

    logger.info("Done: success=%d failed=%d", ok, failed)
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Supermemory documents from an old API key to the active key."
    )
    parser.add_argument(
        "--source-key",
        help="Old Supermemory API key (or set SUPERMEMORY_SOURCE_API_KEY)",
    )
    parser.add_argument(
        "--container-tag",
        default="",
        help=f"Container tag on both sides (default: {settings.resolved_container_tag})",
    )
    parser.add_argument("--dry-run", action="store_true", help="List what would copy")
    parser.add_argument("--limit", type=int, default=0, help="Max docs (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-copy even if customId exists")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between uploads (default: 0.5)",
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
