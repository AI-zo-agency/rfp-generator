#!/usr/bin/env python3
"""
Re-ingest ONLY the team bio PDFs from Google Drive to fix content extraction issues.

The bio PDFs (04_Bio_*.pdf) were previously ingested but Supermemory only captured
"Loading..." pages instead of actual content. This script re-ingests them using
the upload mode (downloads PDF and uploads to Supermemory) to ensure proper extraction.

Usage:
  cd backend && source .venv/bin/activate
  python scripts/reingest_bio_pdfs.py --folder-id "YOUR_KNOWLEDGE_BASE_FOLDER_ID"
  
  # Dry run to see what would be re-ingested
  python scripts/reingest_bio_pdfs.py --folder-id "YOUR_FOLDER_ID" --dry-run
  
  # If you know the folder name
  python scripts/reingest_bio_pdfs.py --folder-name "6. RFP CLAUDE Specialis"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from ingest_drive_folder_to_supermemory import (
    _drive_service,
    find_folder_id_by_name,
    list_folder_files,
    ingest_parallel_uploads,
)
from app.services import supermemory

logger = logging.getLogger("reingest_bios")


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

    # Get all files in the folder
    all_files = list_folder_files(service, folder_id)
    
    # Filter to ONLY bio files (04_Bio_*.pdf)
    bio_files = [
        f for f in all_files
        if f.name.startswith("04_Bio_") and f.name.endswith(".pdf")
    ]

    if not bio_files:
        logger.warning("No bio files (04_Bio_*.pdf) found in folder %s", folder_id)
        return 0

    logger.info("Found %d bio files to re-ingest:", len(bio_files))
    for bf in bio_files:
        logger.info("  - %s", bf.name)

    if args.dry_run:
        logger.info("Dry run complete. Use without --dry-run to re-ingest.")
        return 0

    logger.info("\nRe-ingesting bio files using upload mode (ensures proper PDF extraction)...")
    
    # Use upload mode to ensure PDFs are properly downloaded and extracted
    ok, failed = await ingest_parallel_uploads(
        bio_files,
        folder_id=folder_id,
        dry_run=False,
        workers=args.workers,
    )

    logger.info("\n" + "="*60)
    logger.info("RESULTS: success=%d failed=%d", ok, failed)
    
    if ok > 0:
        logger.info("\n✅ Bio files successfully re-ingested!")
        logger.info("Now test the proposal generation to see if [VERIFY] placeholders are gone.")
        logger.info("\nTest with:")
        logger.info('  python3 -c "')
        logger.info('  import asyncio')
        logger.info('  from app.services import supermemory')
        logger.info('  async def test():')
        logger.info('      hits = await supermemory.search_hybrid(')
        logger.info('          query=\"Sonja Anderson years experience education\", limit=5')
        logger.info('      )')
        logger.info('      for hit in hits:')
        logger.info('          print(hit.get(\"metadata\", {}).get(\"fileName\"))')
        logger.info('          print(supermemory.hit_text(hit)[:200])')
        logger.info('  asyncio.run(test())')
        logger.info('  "')
    
    if failed > 0:
        logger.error("\n❌ Some files failed to re-ingest. Check logs above.")
        return 1
    
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-ingest team bio PDFs to fix content extraction issues"
    )
    parser.add_argument("--folder-id", help="Google Drive folder ID containing bio PDFs")
    parser.add_argument("--folder-name", help='Folder name, e.g. "6. RFP CLAUDE Specialis"')
    parser.add_argument("--dry-run", action="store_true", help="Show what would be re-ingested")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel uploads (default: 4)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
