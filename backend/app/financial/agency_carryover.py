"""Diff open Agency items week-over-week for carryover aging."""

from __future__ import annotations

from typing import Any

from app.financial.agency_week import iso


def _index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in items if row.get("id")}


def apply_carryover_state(
    current_items: list[dict[str, Any]],
    prior_open: list[dict[str, Any]] | None,
    *,
    week_start: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return enriched open_items, carryover, resolved, new lists."""
    prior_by_id = _index(prior_open or [])
    enriched: list[dict[str, Any]] = []
    carryover: list[dict[str, Any]] = []
    new_items: list[dict[str, Any]] = []

    for item in current_items:
        item_id = str(item["id"])
        prior = prior_by_id.get(item_id)
        row = {**item}
        if prior:
            first_seen = str(prior.get("first_seen_week") or week_start)
            weeks_open = int(prior.get("weeks_open") or 1) + 1
            row["first_seen_week"] = first_seen
            row["weeks_open"] = weeks_open
            row["carryover"] = True
            carryover.append(row)
        else:
            row["first_seen_week"] = week_start
            row["weeks_open"] = 1
            row["carryover"] = False
            new_items.append(row)
        enriched.append(row)

    current_ids = {str(row["id"]) for row in current_items}
    resolved = [
        {**row, "carryover": False}
        for item_id, row in prior_by_id.items()
        if item_id not in current_ids
    ]
    return enriched, carryover, resolved, new_items
