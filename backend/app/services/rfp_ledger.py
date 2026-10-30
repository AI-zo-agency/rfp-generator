"""Read-only RFP ledger: who ran, tweaked, opened and deleted what.

Built on the fly from `user_activity_events` (workspace rfp, entity_type rfp)
and `proposal_tweak_events`. It never reads the RFPs table, so a deleted RFP
stays visible, titled from the activity rows. List and detail results carry
counts and metadata only; before/after text is served by `get_tweak` alone.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services import proposal_tweaks_store as tweak_store
from app.services.llm_call_log import get_run_total_cost_usd
from app.services.user_activity import OPEN_ACTION, list_activity

logger = logging.getLogger(__name__)

MAX_ROWS = 5000
_COST_TTL_SECONDS = 60.0
_cost_cache: dict[str, tuple[float, float]] = {}

_RUN_PREFIXES = ("proposal.phase_", "rfp.gonogo_")
_OUTCOME_BY_ACTION = {
    "proposal.phase_completed": "completed",
    "proposal.phase_failed": "failed",
    "proposal.phase_stopped": "cancelled",
    "rfp.gonogo_completed": "completed",
    "rfp.gonogo_failed": "failed",
}
_COUNTED_BUCKETS = ("manual", "ai", "other")


# Accounts from other companies (e.g. the CGN login) are not ZO team members;
# their rows still count, but are shown as "system" instead of a person.
_FOREIGN_ACTOR_DOMAINS = ("contractorgrowthnetwork.com",)


def _mask_foreign_actor(row: dict[str, Any]) -> dict[str, Any]:
    email = str(row.get("actor_email") or "").strip().lower()
    if email.endswith(tuple("@" + d for d in _FOREIGN_ACTOR_DOMAINS)):
        return {**row, "actor_email": "system"}
    return row


def _fetch_tweaks(**kwargs: Any) -> list[dict[str, Any]]:
    return [_mask_foreign_actor(t) for t in tweak_store.list_tweaks(limit=MAX_ROWS, **kwargs)]


def _parse_ts(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value or "")
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_cost(llm_run_id: str) -> float:
    cached = _cost_cache.get(llm_run_id)
    now = time.monotonic()
    if cached and now - cached[0] < _COST_TTL_SECONDS:
        return cached[1]
    try:
        value = float(get_run_total_cost_usd(llm_run_id) or 0.0)
    except Exception:  # noqa: BLE001 — a cost lookup must not break the ledger
        logger.exception("rfp_ledger: cost lookup failed for %s", llm_run_id)
        value = 0.0
    _cost_cache[llm_run_id] = (now, value)
    return value


def _fetch_rows(
    *, entity_id: str | None = None, from_ts: str | None = None, to_ts: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """RFP-entity activity rows, oldest first, plus whether the row cap was hit."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    truncated = False
    while True:
        page = list_activity(
            workspace="rfp", limit=100, cursor=cursor, entity_id=entity_id,
            from_ts=from_ts, to_ts=to_ts, with_stats=False,
        )
        rows.extend(
            _mask_foreign_actor(r)
            for r in page["items"]
            if r.get("entity_type") == "rfp" and r.get("entity_id")
        )
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
    rows.reverse()
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows, truncated


@dataclass
class _Rfp:
    rfp_id: str
    title: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    tweaks: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    deleted_by: str | None = None
    deleted_at: str | None = None
    opens: list[dict[str, Any]] = field(default_factory=list)


def _build_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        action = str(row.get("action") or "")
        if action.startswith(_RUN_PREFIXES):
            groups.setdefault(str(row.get("run_id") or f"row:{row['id']}"), []).append(row)
    runs: list[dict[str, Any]] = []
    for run_id, group in groups.items():
        kind = "gonogo" if str(group[0]["action"]).startswith("rfp.gonogo_") else "proposal"
        starts = [r for r in group if r.get("outcome") == "started"]
        owner = (starts or group)[0]["actor_email"]
        phases: list[dict[str, Any]] = []
        for row in group:
            phase = "go-no-go" if kind == "gonogo" else str((row.get("metadata") or {}).get("phase") or "")
            if row.get("outcome") == "started":
                phases.append({"phase": phase, "started_at": row["created_at"],
                               "finished_at": None, "outcome": "running"})
                continue
            outcome = _OUTCOME_BY_ACTION.get(str(row["action"]), "completed")
            open_phase = next(
                (p for p in reversed(phases) if p["phase"] == phase and p["finished_at"] is None), None
            )
            if open_phase is None:
                phases.append({"phase": phase, "started_at": row["created_at"],
                               "finished_at": row["created_at"], "outcome": outcome})
            else:
                open_phase["finished_at"] = row["created_at"]
                open_phase["outcome"] = outcome
        last = group[-1]
        outcome = "running" if last.get("outcome") == "started" else _OUTCOME_BY_ACTION.get(
            str(last["action"]), "completed"
        )
        started_at = group[0]["created_at"]
        finished_at = None if outcome == "running" else last["created_at"]
        first, end = _parse_ts(started_at), _parse_ts(finished_at)
        stopped_by = next(
            (r["actor_email"] for r in group
             if r["action"] == "proposal.phase_stopped" and r["actor_email"] != owner),
            None,
        )
        llm_ids = {str((r.get("metadata") or {}).get("llm_run_id") or "") for r in group} - {""}
        runs.append(
            {
                "run_id": run_id,
                "kind": kind,
                "owner": owner,
                "started_at": started_at,
                "finished_at": finished_at,
                "outcome": outcome,
                "duration_sec": int((end - first).total_seconds()) if first and end else None,
                "cost_usd": round(sum(_run_cost(i) for i in sorted(llm_ids)), 4),
                "phases": phases,
                "stopped_by": stopped_by,
            }
        )
    runs.sort(key=lambda r: r["started_at"], reverse=True)
    return runs


def _analyze(rows: list[dict[str, Any]], tweaks: list[dict[str, Any]]) -> dict[str, _Rfp]:
    """Group rows and tweaks by RFP id (rows must be oldest first)."""
    by_id: dict[str, _Rfp] = {}
    for row in rows:
        rfp = by_id.setdefault(str(row["entity_id"]), _Rfp(str(row["entity_id"])))
        rfp.rows.append(row)
        rfp.title = str(row.get("entity_label") or "") or rfp.title
        if row["action"] == OPEN_ACTION:
            rfp.opens.append(row)
        elif row["action"] == "rfp.deleted":
            rfp.deleted_by, rfp.deleted_at = row["actor_email"], row["created_at"]
    for tweak in tweaks:
        rfp = by_id.setdefault(str(tweak["rfp_id"]), _Rfp(str(tweak["rfp_id"])))
        rfp.tweaks.append(tweak)
        rfp.title = rfp.title or str(tweak.get("rfp_title") or "")
    for rfp in by_id.values():
        rfp.title = rfp.title or rfp.rfp_id
        rfp.runs = _build_runs(rfp.rows)
    return by_id


def _actors(rfp: _Rfp) -> set[str]:
    return {r["actor_email"] for r in rfp.rows} | {t["actor_email"] for t in rfp.tweaks}


def _last_activity(rfp: _Rfp) -> str:
    stamps = [r["created_at"] for r in rfp.rows] + [t["updated_at"] for t in rfp.tweaks]
    return max(stamps) if stamps else ""


def _ledger_row(rfp: _Rfp) -> dict[str, Any]:
    counted = [t for t in rfp.tweaks if t["bucket"] in _COUNTED_BUCKETS]
    touches = [(r["created_at"], r["actor_email"]) for r in rfp.rows
               if r["action"] != OPEN_ACTION and r["actor_email"] != "system"]
    touches += [(t["updated_at"], t["actor_email"]) for t in rfp.tweaks]
    last_touch = max(touches) if touches else (None, None)
    last_open = rfp.opens[-1] if rfp.opens else None
    ran_by: list[str] = []
    for run in reversed(rfp.runs):
        if run["owner"] not in ran_by:
            ran_by.append(run["owner"])
    return {
        "rfp_id": rfp.rfp_id,
        "title": rfp.title,
        "deleted": rfp.deleted_at is not None,
        "deleted_by": rfp.deleted_by,
        "deleted_at": rfp.deleted_at,
        "ran_by": ran_by,
        "last_touched_by": last_touch[1],
        "last_touched_at": last_touch[0],
        "last_opened_by": last_open["actor_email"] if last_open else None,
        "last_opened_at": last_open["created_at"] if last_open else None,
        "opened_by_count": len({o["actor_email"] for o in rfp.opens}),
        "run_count": len(rfp.runs),
        "cost_usd": round(sum(r["cost_usd"] for r in rfp.runs), 4),
        "tweak_count": len(counted),
        "sections_touched": len({t["section_id"] for t in counted}),
        "words_added": sum(t["words_added"] for t in counted),
        "words_removed": sum(t["words_removed"] for t in counted),
        "manual_count": sum(1 for t in counted if t["bucket"] == "manual"),
        "ai_count": sum(1 for t in counted if t["bucket"] == "ai"),
        "other_count": sum(1 for t in counted if t["bucket"] == "other"),
        "restore_count": sum(1 for t in rfp.tweaks if t["bucket"] == "restore"),
    }


def list_rfp_ledger(
    *,
    person: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    include_deleted: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    rows, truncated = _fetch_rows(from_ts=from_ts, to_ts=to_ts)
    tweaks = _fetch_tweaks(from_ts=from_ts, to_ts=to_ts)
    who = (person or "").strip().lower()
    items = []
    for rfp in sorted(_analyze(rows, tweaks).values(), key=_last_activity, reverse=True):
        if who and who not in _actors(rfp):
            continue
        row = _ledger_row(rfp)
        if row["deleted"] and not include_deleted:
            continue
        items.append(row)
    cap = max(1, min(int(limit or 100), 500))
    return {"items": items[:cap], "truncated": truncated or len(items) > cap}


def get_rfp_ledger_detail(rfp_id: str, *, include_opens: bool = False) -> dict[str, Any]:
    rows, _ = _fetch_rows(entity_id=rfp_id)
    tweaks = _fetch_tweaks(rfp_id=rfp_id)
    rfp = _analyze(rows, tweaks).get(rfp_id)
    if rfp is None:
        return {"rfp": None, "runs": [], "tweaks": [], "viewers": [], "opens": []}
    viewers: dict[str, dict[str, Any]] = {}
    for open_row in rfp.opens:
        who = viewers.setdefault(
            open_row["actor_email"],
            {"actor_email": open_row["actor_email"], "last_opened_at": "", "open_count": 0},
        )
        who["open_count"] += 1
        who["last_opened_at"] = open_row["created_at"]
    return {
        "rfp": _ledger_row(rfp),
        "runs": rfp.runs,
        "tweaks": tweaks,
        "viewers": sorted(viewers.values(), key=lambda v: v["last_opened_at"], reverse=True),
        "opens": (
            [{"id": o["id"], "actor_email": o["actor_email"], "created_at": o["created_at"]}
             for o in reversed(rfp.opens)]
            if include_opens else []
        ),
    }


def list_people(*, from_ts: str | None = None, to_ts: str | None = None) -> dict[str, Any]:
    rows, _ = _fetch_rows(from_ts=from_ts, to_ts=to_ts)
    tweaks = _fetch_tweaks(from_ts=from_ts, to_ts=to_ts)
    people: dict[str, dict[str, Any]] = {}

    def person(email: str) -> dict[str, Any]:
        return people.setdefault(
            email,
            {"actor_email": email, "ran": set(), "tweaked": set(), "opened": set(),
             "deleted": set(), "runs": 0, "tweaks": 0, "cost_usd": 0.0},
        )

    for rfp in _analyze(rows, tweaks).values():
        for run in rfp.runs:
            p = person(run["owner"])
            p["ran"].add(rfp.rfp_id)
            p["runs"] += 1
            p["cost_usd"] += run["cost_usd"]
        for tweak in rfp.tweaks:
            if tweak["bucket"] in _COUNTED_BUCKETS:
                p = person(tweak["actor_email"])
                p["tweaked"].add(rfp.rfp_id)
                p["tweaks"] += 1
        for open_row in rfp.opens:
            person(open_row["actor_email"])["opened"].add(rfp.rfp_id)
        if rfp.deleted_by:
            person(rfp.deleted_by)["deleted"].add(rfp.rfp_id)
    items = [
        {
            "actor_email": email,
            "rfps_ran": len(p["ran"]),
            "rfps_tweaked": len(p["tweaked"]),
            "rfps_opened": len(p["opened"]),
            "rfps_deleted": len(p["deleted"]),
            "runs": p["runs"],
            "tweaks": p["tweaks"],
            "cost_usd": round(p["cost_usd"], 4),
        }
        for email, p in people.items()
        if email != "system"
    ]
    items.sort(key=lambda i: (i["runs"] + i["tweaks"] + i["rfps_opened"], i["actor_email"]), reverse=True)
    return {"items": items}
