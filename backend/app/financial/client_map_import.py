"""Import Tags sheet rows into client_map (additive seed)."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.financial import client_map_repository as repo
from app.financial import google_sheets

logger = logging.getLogger(__name__)

ZO_DASHBOARD_ID = "1W4KQuDSu5uUFRiTLoEpYgPCdWHJ-s0wo_EYIdlDAgBQ"
TAGS_RANGE = "🔤Tags!A1:I500"


def _cell(row: list[Any], idx: int) -> str:
    if idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _optional(value: str) -> str | None:
    return value or None


def collapse_tag_rows(raw: list[list[Any]]) -> list[dict[str, Any]]:
    """Parse sheet rows, dedupe exact (tag, client) pairs, keep tag collisions."""
    if not raw:
        return []

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []

    for row in raw[1:]:
        tag_code = _cell(row, 0)
        client_name = _cell(row, 1)
        if not tag_code and not client_name:
            continue

        key = (tag_code.upper(), client_name.casefold())
        if key in seen:
            continue
        seen.add(key)

        is_internal = tag_code.upper() == "ZOA" or client_name.casefold() == "zo agency"
        out.append(
            {
                "tag_code": tag_code,
                "client_name": client_name,
                "city": _optional(_cell(row, 2)),
                "state": _optional(_cell(row, 3)),
                "current_am": _optional(_cell(row, 5)),
                "status": _optional(_cell(row, 6)),
                "source": _optional(_cell(row, 7)),
                "highest_value": _optional(_cell(row, 8)),
                "is_internal": is_internal,
            }
        )

    return out


def rows_to_insert(
    collapsed: list[dict[str, Any]],
    *,
    existing_codes: set[str],
) -> list[dict[str, Any]]:
    """Return rows whose tag_code is not already present (case-insensitive)."""
    existing = {c.upper() for c in existing_codes}
    return [r for r in collapsed if r["tag_code"].upper() not in existing]


def _count_collisions_kept(collapsed: list[dict[str, Any]]) -> int:
    tag_counts = Counter(r["tag_code"].upper() for r in collapsed)
    return sum(1 for r in collapsed if tag_counts[r["tag_code"].upper()] > 1)


def import_tags_sheet() -> dict[str, int]:
    """Fetch Tags tab, collapse, skip existing tag codes, insert new rows."""
    raw = google_sheets.fetch_live_sheet_rows(ZO_DASHBOARD_ID, TAGS_RANGE)
    if raw is None:
        logger.error(
            "operation=import_tags_sheet fetch_failed spreadsheet_id=%s range=%s",
            ZO_DASHBOARD_ID,
            TAGS_RANGE,
        )
        return {"inserted": 0, "skipped": 0, "collisions_kept": 0}

    collapsed = collapse_tag_rows(raw)
    existing = {c.upper() for c in repo.existing_tag_codes()}
    to_add = rows_to_insert(collapsed, existing_codes=existing)
    skipped = len(collapsed) - len(to_add)
    collisions_kept = _count_collisions_kept(collapsed)

    inserted = 0
    for row in to_add:
        repo.insert_client_map(row)
        inserted += 1

    logger.info(
        "operation=import_tags_sheet inserted=%s skipped=%s collisions_kept=%s raw_rows=%s collapsed=%s",
        inserted,
        skipped,
        collisions_kept,
        len(raw) - 1,
        len(collapsed),
    )
    return {
        "inserted": inserted,
        "skipped": skipped,
        "collisions_kept": collisions_kept,
    }
