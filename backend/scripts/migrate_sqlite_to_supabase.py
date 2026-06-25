#!/usr/bin/env python3
"""One-off: migrate frontend/data/rfps.db → Supabase Postgres."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services import supabase_db as sb  # noqa: E402


def _sqlite_path(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    return settings.database_path


def _rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(f"SELECT * FROM {table}").fetchall())


def _rfp_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    analysis = data.get("go_no_go_analysis")
    if analysis and isinstance(analysis, str):
        try:
            data["go_no_go_analysis"] = json.loads(analysis)
        except json.JSONDecodeError:
            data["go_no_go_analysis"] = None
    return data


def migrate(*, sqlite_file: Path, dry_run: bool) -> None:
    if not sb.use_supabase_db():
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in backend/.env")
        sys.exit(1)

    if not sqlite_file.is_file():
        print(f"ERROR: SQLite file not found: {sqlite_file}")
        sys.exit(1)

    conn = sqlite3.connect(sqlite_file)
    conn.row_factory = sqlite3.Row

    client = sb._get_client()

    rfps = _rows(conn, "rfps")
    research = _rows(conn, "proposal_research") if _table_exists(conn, "proposal_research") else []
    drafts = _rows(conn, "proposal_drafts") if _table_exists(conn, "proposal_drafts") else []
    jobs = _rows(conn, "sync_jobs") if _table_exists(conn, "sync_jobs") else []

    print(f"Found: {len(rfps)} rfps, {len(research)} research, {len(drafts)} drafts, {len(jobs)} sync_jobs")

    if dry_run:
        print("Dry run — no writes.")
        return

    for row in rfps:
        client.table("rfps").upsert(_rfp_row(row), on_conflict="id").execute()
    print(f"Migrated {len(rfps)} rfps")

    for row in research:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        client.table("proposal_research").upsert(
            {
                "rfp_id": row["rfp_id"],
                "payload": payload,
                "updated_at": row["updated_at"],
            },
            on_conflict="rfp_id",
        ).execute()
    print(f"Migrated {len(research)} proposal_research rows")

    for row in drafts:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        client.table("proposal_drafts").upsert(
            {
                "rfp_id": row["rfp_id"],
                "payload": payload,
                "updated_at": row["updated_at"],
            },
            on_conflict="rfp_id",
        ).execute()
    print(f"Migrated {len(drafts)} proposal_drafts rows")

    for row in jobs:
        client.table("sync_jobs").upsert(dict(row), on_conflict="id").execute()
    print(f"Migrated {len(jobs)} sync_jobs")

    print("Done.")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite rfps.db to Supabase")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Path to rfps.db (default: DATABASE_PATH from settings)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(sqlite_file=_sqlite_path(args.sqlite), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
