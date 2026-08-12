#!/usr/bin/env python3
"""
Dry-run: find duplicate RFPs already stored in ZO.

Detects:
  1. Same JustWin external_id (or same rfp-jw-{id})
  2. Same title + received_date
  3. Same title (normalized) across any source — catches manual + JustWin copies
  4. Fuzzy near-duplicate titles (SequenceMatcher >= 0.85)

Does NOT delete or modify anything.

Usage:
  cd backend && source .venv/bin/activate
  python scripts/find_duplicate_rfps.py              # all sources (recommended)
  python scripts/find_duplicate_rfps.py --json
  python scripts/find_duplicate_rfps.py --source justwin   # misses manual↔JW dupes
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services import supabase_db as sb  # noqa: E402


def _load_rows_supabase() -> list[dict[str, Any]]:
    client = sb._get_client()
    rows: list[dict[str, Any]] = []
    page_size = 1000
    offset = 0
    while True:
        result = (
            client.table("rfps")
            .select(
                "id,external_id,title,client,source,received_date,due_date,"
                "stage,status,pdf_path,synced_at,justwin_tab"
            )
            .order("synced_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _load_rows_sqlite(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(rfps)").fetchall()
        }
        select_cols = [
            c
            for c in (
                "id",
                "external_id",
                "title",
                "client",
                "source",
                "received_date",
                "due_date",
                "stage",
                "status",
                "pdf_path",
                "synced_at",
                "justwin_tab",
            )
            if c in cols
        ]
        sql = f"SELECT {', '.join(select_cols)} FROM rfps"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _norm_title(title: str | None) -> str:
    """Normalize for duplicate detection across manual vs JustWin imports."""
    text = (title or "").strip().casefold()
    text = text.replace("ʻ", "'").replace("ʼ", "'").replace("&", " and ")
    text = re.sub(r"\s*\[[a-z]{2}\]\s*$", "", text)  # trailing [CA]
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _norm_date(value: str | None) -> str:
    return (value or "").strip()[:10]


def _norm_external(row: dict[str, Any]) -> str:
    ext = (row.get("external_id") or "").strip()
    if ext and not str(ext).startswith("manual-"):
        return ext
    rid = (row.get("id") or "").strip()
    if rid.startswith("rfp-jw-"):
        return rid[len("rfp-jw-") :]
    return ""


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "external_id": row.get("external_id"),
        "title": row.get("title"),
        "client": row.get("client"),
        "source": row.get("source"),
        "received_date": row.get("received_date"),
        "due_date": row.get("due_date"),
        "stage": row.get("stage"),
        "status": row.get("status"),
        "has_pdf": bool((row.get("pdf_path") or "").strip())
        and not str(row.get("pdf_path") or "").startswith("pending:"),
        "pdf_path": row.get("pdf_path"),
        "synced_at": row.get("synced_at"),
        "justwin_tab": row.get("justwin_tab"),
    }


def _groups_with_dupes(
    rows: list[dict[str, Any]],
    key_fn,
) -> list[tuple[str, list[dict[str, Any]]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        if not key:
            continue
        buckets[key].append(row)
    return sorted(
        ((k, v) for k, v in buckets.items() if len(v) > 1),
        key=lambda item: (-len(item[1]), item[0]),
    )


def _fuzzy_pairs(
    rows: list[dict[str, Any]],
    *,
    threshold: float = 0.85,
) -> list[dict[str, Any]]:
    indexed = [(_norm_title(r.get("title")), r) for r in rows]
    pairs: list[dict[str, Any]] = []
    for i in range(len(indexed)):
        a_key, a = indexed[i]
        if not a_key:
            continue
        for j in range(i + 1, len(indexed)):
            b_key, b = indexed[j]
            if not b_key or a_key == b_key:
                continue
            ratio = SequenceMatcher(None, a_key, b_key).ratio()
            if ratio >= threshold:
                pairs.append(
                    {
                        "similarity": round(ratio, 3),
                        "rows": [_row_summary(a), _row_summary(b)],
                    }
                )
    pairs.sort(key=lambda p: -p["similarity"])
    return pairs


def find_duplicates(
    rows: list[dict[str, Any]],
    *,
    source_filter: str | None,
) -> dict[str, Any]:
    if source_filter:
        rows = [
            r
            for r in rows
            if (r.get("source") or "").strip().lower() == source_filter.lower()
        ]

    by_external = _groups_with_dupes(rows, _norm_external)
    by_title_date = _groups_with_dupes(
        rows,
        lambda r: (
            f"{_norm_title(r.get('title'))}|{_norm_date(r.get('received_date'))}"
            if _norm_title(r.get("title")) and _norm_date(r.get("received_date"))
            else ""
        ),
    )
    by_title = _groups_with_dupes(rows, lambda r: _norm_title(r.get("title")))
    fuzzy = _fuzzy_pairs(rows)

    return {
        "total_rfps": len(rows),
        "duplicate_external_id_groups": len(by_external),
        "duplicate_title_date_groups": len(by_title_date),
        "duplicate_title_only_groups": len(by_title),
        "fuzzy_pair_count": len(fuzzy),
        "by_external_id": [
            {"key": k, "count": len(v), "rows": [_row_summary(r) for r in v]}
            for k, v in by_external
        ],
        "by_title_and_received_date": [
            {"key": k, "count": len(v), "rows": [_row_summary(r) for r in v]}
            for k, v in by_title_date
        ],
        "by_title_only": [
            {"key": k, "count": len(v), "rows": [_row_summary(r) for r in v]}
            for k, v in by_title
        ],
        "fuzzy_title_pairs": fuzzy,
    }


def _print_group(label: str, groups: list[dict[str, Any]]) -> None:
    print(f"\n=== {label}: {len(groups)} group(s) ===")
    if not groups:
        print("  (none)")
        return
    for i, group in enumerate(groups, 1):
        print(f"\n  [{i}] key={group['key']!r}  count={group['count']}")
        for row in group["rows"]:
            pdf = "pdf" if row["has_pdf"] else "no-pdf"
            print(
                f"      - {row['id']}"
                f"  src={row['source']}"
                f"  recv={row['received_date'] or '-'}"
                f"  stage={row['stage']}"
                f"  {pdf}"
                f"  synced={row['synced_at'] or '-'}"
            )
            print(f"        title: {row['title']}")
            if row.get("client"):
                print(f"        client: {row['client']}")


def _print_fuzzy(pairs: list[dict[str, Any]]) -> None:
    print(f"\n=== Fuzzy near-duplicate titles: {len(pairs)} pair(s) ===")
    if not pairs:
        print("  (none)")
        return
    for i, pair in enumerate(pairs, 1):
        print(f"\n  [{i}] similarity={pair['similarity']}")
        for row in pair["rows"]:
            print(
                f"      - {row['id']}  src={row['source']}  "
                f"recv={row['received_date'] or '-'}  title={row['title']!r}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run: list duplicate RFPs (no writes)."
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Read from this SQLite rfps.db instead of Supabase",
    )
    parser.add_argument(
        "--source",
        choices=("justwin", "manual", "all"),
        default="all",
        help="Filter by source (default: all — needed to catch manual↔JustWin dupes)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report",
    )
    args = parser.parse_args()

    if args.sqlite:
        if not args.sqlite.is_file():
            print(f"ERROR: SQLite file not found: {args.sqlite}", file=sys.stderr)
            return 1
        print(f"DRY RUN — reading SQLite: {args.sqlite}")
        rows = _load_rows_sqlite(args.sqlite)
        backend = "sqlite"
    else:
        if not sb.use_supabase_db():
            print(
                "ERROR: Supabase not configured. Set SUPABASE_URL / "
                "SUPABASE_SERVICE_ROLE_KEY in backend/.env, or pass --sqlite PATH",
                file=sys.stderr,
            )
            return 1
        print("DRY RUN — reading Supabase rfps (no writes)")
        rows = _load_rows_supabase()
        backend = "supabase"

    source_filter = None if args.source == "all" else args.source
    report = find_duplicates(rows, source_filter=source_filter)
    report["backend"] = backend
    report["source_filter"] = args.source

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"Loaded {report['total_rfps']} RFP row(s) (source={args.source})")
    if args.source != "all":
        print(
            "NOTE: --source filter can hide manual↔JustWin duplicates. "
            "Re-run with no --source (or --source all) for a full check."
        )
    _print_group("Same external_id", report["by_external_id"])
    _print_group(
        "Same title + received_date",
        report["by_title_and_received_date"],
    )
    _print_group(
        "Same title (any date / any source)",
        report["by_title_only"],
    )
    _print_fuzzy(report["fuzzy_title_pairs"])

    print("\n--- summary ---")
    print(f"Same-title duplicate groups: {report['duplicate_title_only_groups']}")
    print(f"Fuzzy near-duplicate pairs: {report['fuzzy_pair_count']}")
    print("No changes made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
