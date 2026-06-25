#!/usr/bin/env python3
"""Upload local RFP PDFs to Supabase Storage and update pdf_path in Postgres."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services import supabase_db as sb  # noqa: E402
from app.services import supabase_storage  # noqa: E402
from app.services.rfp_repository import list_rfps  # noqa: E402
from app.services.rfp_storage import (  # noqa: E402
    is_supabase_path,
    resolve_local_pdf_path,
    rfp_object_key,
    to_supabase_path,
)


def ensure_bucket() -> None:
    if not supabase_storage.is_configured():
        print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env")
        sys.exit(1)

    bucket = settings.supabase_rfp_bucket
    client = supabase_storage._get_client()
    try:
        client.storage.create_bucket(
            bucket,
            options={"public": False, "file_size_limit": 52428800},
        )
        print(f"Created bucket: {bucket}")
    except Exception as exc:
        message = str(exc).lower()
        if "already exists" in message or "duplicate" in message:
            print(f"Bucket already exists: {bucket}")
        else:
            print(f"Note: could not create bucket ({exc}) — create '{bucket}' manually in Supabase Storage if needed.")


def migrate_pdfs(*, dry_run: bool) -> None:
    ensure_bucket()

    rfps = list_rfps()
    with_pdf = [r for r in rfps if r.pdf_path]
    uploaded = 0
    skipped = 0
    failed = 0

    print(f"Found {len(rfps)} rfps, {len(with_pdf)} with pdf_path set")

    for rfp in rfps:
        if rfp.pdf_path and is_supabase_path(rfp.pdf_path):
            print(f"  skip (already in bucket): {rfp.id}")
            skipped += 1
            continue

        local = resolve_local_pdf_path(rfp.id, rfp.pdf_path)
        if not local or not local.is_file():
            if rfp.pdf_path:
                print(f"  skip (file missing): {rfp.id} path={rfp.pdf_path!r}")
            skipped += 1
            continue

        key = rfp_object_key(rfp.id)
        supabase_path = to_supabase_path(key)
        size_kb = local.stat().st_size // 1024
        print(f"  upload: {rfp.id} ({size_kb} KB) → {supabase_path}")

        if dry_run:
            uploaded += 1
            continue

        try:
            content = local.read_bytes()
            supabase_storage.upload_pdf(object_key=key, content=content)
            if sb.use_supabase_db():
                sb.update_rfp_pdf_path(rfp.id, supabase_path)
            else:
                from app.services.rfp_repository import update_rfp_pdf_path

                update_rfp_pdf_path(rfp.id, supabase_path)
            uploaded += 1
        except Exception as exc:
            print(f"  FAILED {rfp.id}: {exc}")
            failed += 1

    print(f"\nDone: {uploaded} uploaded, {skipped} skipped, {failed} failed (dry_run={dry_run})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload local RFP PDFs to Supabase Storage bucket"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"Bucket: {settings.supabase_rfp_bucket}")
    print(f"PDF root: {settings.pdf_storage_path}")
    migrate_pdfs(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
