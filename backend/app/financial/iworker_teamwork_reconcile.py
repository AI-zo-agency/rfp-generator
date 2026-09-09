"""Reconcile iWorker sheet hours vs Teamwork timelogs for the same period.

Todd/Ella ask (Aug 31): flag when Murilo / Marcelle / Kelvin (and other iWorker
contractors) log different hours in Teamwork than in the Sonja iWorker sheet.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable

from app.financial.iworker_period_insights import build_period_metrics, parse_entry_date

logger = logging.getLogger(__name__)

TOLERANCE_HOURS = 0.5
STATUS_MATCH = "match"
STATUS_MISMATCH = "mismatch"
STATUS_IWORKER_ONLY = "iworker_only"
STATUS_NO_TEAMWORK_MATCH = "no_teamwork_match"


def normalize_person_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def names_match(a: str, b: str) -> bool:
    """Match display names by equality or token containment (Murilo ⊆ Murilo Mendes)."""
    na, nb = normalize_person_name(a), normalize_person_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return bool(shorter) and shorter <= longer


def match_teamwork_person(
    contractor: str,
    teamwork_people: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the best Teamwork person row for an iWorker contractor name."""
    exact = [
        p for p in teamwork_people if normalize_person_name(str(p.get("name") or "")) == normalize_person_name(contractor)
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return max(exact, key=lambda p: float(p.get("hours") or 0))
    fuzzy = [p for p in teamwork_people if names_match(contractor, str(p.get("name") or ""))]
    if not fuzzy:
        return None
    return max(fuzzy, key=lambda p: float(p.get("hours") or 0))


def _hours_from_minutes(minutes: int | float | None) -> float:
    return round(float(minutes or 0) / 60.0, 2)


def summarize_teamwork_people(timelog_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate Teamwork timelog rows into per-person hours + project breakdown."""
    by_person: dict[str, dict[str, Any]] = {}
    for row in timelog_rows:
        user_id = str(row.get("user_id") or "").strip()
        if not user_id:
            continue
        minutes = int(row.get("minutes") or 0)
        name = str(row.get("user_name") or user_id).strip()
        project_name = str(row.get("project_name") or "").strip() or "Unknown project"
        bucket = by_person.setdefault(
            user_id,
            {"id": user_id, "name": name, "minutes": 0, "projects": {}},
        )
        bucket["minutes"] += minutes
        if name and bucket["name"] in ("", user_id):
            bucket["name"] = name
        proj = bucket["projects"].setdefault(project_name, 0)
        bucket["projects"][project_name] = proj + minutes
    out: list[dict[str, Any]] = []
    for bucket in by_person.values():
        projects = [
            {"name": pname, "hours": _hours_from_minutes(mins)}
            for pname, mins in sorted(
                bucket["projects"].items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        out.append(
            {
                "id": bucket["id"],
                "name": bucket["name"],
                "hours": _hours_from_minutes(bucket["minutes"]),
                "projects": projects,
            }
        )
    return sorted(out, key=lambda p: (-p["hours"], p["name"]))


def merge_zero_hour_known_users(
    period_people: list[dict[str, Any]],
    known_people: list[dict[str, Any]],
    roster: list[str],
) -> list[dict[str, Any]]:
    """Keep period hours, but retain Teamwork identities seen recently even at 0h this week.

    Without this, a contractor who logged Teamwork last week and only iWorker this week
    incorrectly shows as \"No Teamwork user\".
    """
    by_id = {str(p.get("id") or ""): dict(p) for p in period_people if p.get("id")}
    for person in known_people:
        pid = str(person.get("id") or "")
        if not pid or pid in by_id:
            continue
        name = str(person.get("name") or "")
        if not any(names_match(r, name) for r in roster):
            continue
        by_id[pid] = {
            "id": pid,
            "name": name,
            "hours": 0.0,
            "projects": [],
        }
        logger.info(
            "operation=iworker_teamwork_reconcile status=identity_retained user_id=%s name=%s",
            pid,
            name,
        )
    return sorted(by_id.values(), key=lambda p: (-float(p.get("hours") or 0), str(p.get("name") or "")))


def load_teamwork_hours_for_period(
    start: date,
    end: date,
    *,
    roster: list[str] | None = None,
    list_timelogs_fn: Callable[..., list[dict[str, Any]]] | None = None,
    site_id: str | None = None,
    identity_lookback_days: int = 90,
    now: date | None = None,
) -> list[dict[str, Any]]:
    """Load mirrored Teamwork timelogs for [start, end] and keep known roster identities at 0h.

    Identity window is [start - lookback, max(end, today)] so past months (e.g. July)
    still recognize users who logged Teamwork later (e.g. August).
    """
    from app.core.config import settings
    from app.financial.iworker_period_insights import today_in_tz
    from app.financial.teamwork.teamwork_map import site_id_from_base_url
    from app.financial.teamwork.teamwork_panels_from_db import list_timelogs

    sid = site_id or site_id_from_base_url(settings.teamwork_base_url)
    if not sid:
        logger.info("operation=iworker_teamwork_reconcile status=no_site_id")
        return []
    fetch = list_timelogs_fn or list_timelogs
    exclusive_end = (end + timedelta(days=1)).isoformat()
    rows = fetch(
        sid,
        time_logged__gte=start.isoformat(),
        time_logged__lt=exclusive_end,
    )
    period_people = summarize_teamwork_people(rows)
    logger.info(
        "operation=iworker_teamwork_reconcile status=teamwork_loaded site_id=%s start=%s end=%s rows=%s people=%s",
        sid,
        start.isoformat(),
        end.isoformat(),
        len(rows),
        len(period_people),
    )
    names = [n for n in (roster or []) if n and str(n).strip()]
    if not names:
        return period_people

    today = now or today_in_tz()
    identity_start = start - timedelta(days=max(identity_lookback_days, 0))
    identity_end = max(end, today)
    identity_exclusive_end = (identity_end + timedelta(days=1)).isoformat()
    if identity_start >= identity_end and not period_people:
        return period_people
    identity_rows = fetch(
        sid,
        time_logged__gte=identity_start.isoformat(),
        time_logged__lt=identity_exclusive_end,
    )
    known_people = summarize_teamwork_people(identity_rows)
    merged = merge_zero_hour_known_users(period_people, known_people, names)
    logger.info(
        "operation=iworker_teamwork_reconcile status=identity_merged identity_start=%s identity_end=%s known=%s merged=%s",
        identity_start.isoformat(),
        identity_end.isoformat(),
        len(known_people),
        len(merged),
    )
    return merged


def _row_status(iworker_hours: float, teamwork_hours: float | None, matched: bool) -> str:
    if not matched:
        return STATUS_NO_TEAMWORK_MATCH if iworker_hours > 0 else STATUS_MATCH
    tw = float(teamwork_hours or 0)
    if abs(iworker_hours - tw) <= TOLERANCE_HOURS:
        return STATUS_MATCH
    if iworker_hours > 0 and tw == 0:
        return STATUS_IWORKER_ONLY
    return STATUS_MISMATCH


def build_reconciliation(
    entries: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    roster: list[str],
    teamwork_people: list[dict[str, Any]] | None = None,
    contractor_filter: str | None = None,
) -> dict[str, Any]:
    """Compare iWorker hours to Teamwork hours for each roster contractor."""
    want = (contractor_filter or "").strip()
    names = [n for n in roster if n and n.strip()]
    if want and want.lower() != "all":
        names = [n for n in names if n.lower() == want.lower()] or [want]

    # Ensure anyone who logged in the period appears even if missing from tabs.
    metrics_names = set()
    for entry in entries:
        parsed = parse_entry_date(str(entry.get("date") or ""))
        if parsed is None or parsed < start or parsed > end:
            continue
        if float(entry.get("hours") or 0) <= 0:
            continue
        name = str(entry.get("contractor") or "").strip()
        if name:
            metrics_names.add(name)
    for name in sorted(metrics_names):
        if not any(n.lower() == name.lower() for n in names):
            if not want or want.lower() == "all" or name.lower() == want.lower():
                names.append(name)

    tw_people = list(teamwork_people or [])
    used_tw_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for contractor in names:
        cur, _ = build_period_metrics(entries, start, end, contractor)
        iw_hours = float(cur["hours"])
        remaining = [p for p in tw_people if str(p.get("id") or "") not in used_tw_ids]
        matched = match_teamwork_person(contractor, remaining)
        tw_hours = float(matched["hours"]) if matched else None
        tw_id = str(matched["id"]) if matched else None
        if tw_id:
            used_tw_ids.add(tw_id)
        status = _row_status(iw_hours, tw_hours, matched is not None)
        delta = None if tw_hours is None else round(iw_hours - tw_hours, 2)
        rows.append(
            {
                "contractor": contractor,
                "iworker_hours": round(iw_hours, 2),
                "teamwork_hours": None if tw_hours is None else round(tw_hours, 2),
                "delta_hours": delta,
                "status": status,
                "teamwork_user_id": tw_id,
                "teamwork_name": str(matched["name"]) if matched else None,
                "teamwork_projects": list(matched.get("projects") or []) if matched else [],
            }
        )

    summary = {
        "matched": sum(1 for r in rows if r["status"] == STATUS_MATCH),
        "mismatched": sum(1 for r in rows if r["status"] == STATUS_MISMATCH),
        "iworker_only": sum(1 for r in rows if r["status"] == STATUS_IWORKER_ONLY),
        "no_teamwork_match": sum(1 for r in rows if r["status"] == STATUS_NO_TEAMWORK_MATCH),
    }
    signals: list[dict[str, Any]] = []
    for row in rows:
        if row["status"] == STATUS_MATCH:
            continue
        name = row["contractor"]
        if row["status"] == STATUS_MISMATCH:
            signals.append(
                {
                    "id": f"iworker:teamwork_mismatch:{name}",
                    "severity": "capacity",
                    "headline": f"{name}: iWorker {row['iworker_hours']}h vs Teamwork {row['teamwork_hours']}h",
                    "detail": (
                        f"Delta {row['delta_hours']:+.1f}h for {start.isoformat()}–{end.isoformat()}. "
                        "Confirm which jobs were logged in Teamwork vs the iWorker sheet."
                    ),
                    "contractor": name,
                }
            )
        elif row["status"] == STATUS_IWORKER_ONLY:
            signals.append(
                {
                    "id": f"iworker:teamwork_missing:{name}",
                    "severity": "capacity",
                    "headline": f"{name} logged {row['iworker_hours']}h in iWorker with 0h in Teamwork",
                    "detail": "They have a Teamwork user, but no timelogs this period — ask them to log matching jobs.",
                    "contractor": name,
                }
            )
        elif row["status"] == STATUS_NO_TEAMWORK_MATCH and row["iworker_hours"] > 0:
            signals.append(
                {
                    "id": f"iworker:teamwork_unmatched:{name}",
                    "severity": "capacity",
                    "headline": f"{name} has iWorker hours but no matching Teamwork user",
                    "detail": "Check Teamwork display name spelling or that they have a user account.",
                    "contractor": name,
                }
            )

    logger.info(
        "operation=iworker_teamwork_reconcile status=built start=%s end=%s rows=%s mismatched=%s",
        start.isoformat(),
        end.isoformat(),
        len(rows),
        summary["mismatched"],
    )
    return {
        "status": "ok",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "tolerance_hours": TOLERANCE_HOURS,
        "rows": rows,
        "summary": summary,
        "signals": signals,
    }


def _empty_payload(
    *,
    status: str,
    detail: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    return {
        "status": status,
        "detail": detail,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "tolerance_hours": TOLERANCE_HOURS,
        "rows": [],
        "summary": {
            "matched": 0,
            "mismatched": 0,
            "iworker_only": 0,
            "no_teamwork_match": 0,
        },
        "signals": [],
    }


def reconcile_iworker_vs_teamwork(
    entries: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    roster: list[str],
    contractor_filter: str | None = None,
    list_timelogs_fn: Callable[..., list[dict[str, Any]]] | None = None,
    site_id: str | None = None,
) -> dict[str, Any]:
    """End-to-end reconcile; unavailable when Teamwork base URL is not configured."""
    from app.core.config import settings
    from app.financial.teamwork.teamwork_map import site_id_from_base_url

    sid = site_id or site_id_from_base_url(settings.teamwork_base_url)
    if not sid:
        return _empty_payload(
            status="unavailable",
            detail="Teamwork is not configured — connect Teamwork to compare hours.",
            start=start,
            end=end,
        )

    try:
        tw_people = load_teamwork_hours_for_period(
            start,
            end,
            roster=roster,
            list_timelogs_fn=list_timelogs_fn,
            site_id=sid,
        )
    except Exception:
        logger.exception(
            "operation=iworker_teamwork_reconcile status=teamwork_load_failed start=%s end=%s",
            start.isoformat(),
            end.isoformat(),
        )
        return _empty_payload(
            status="error",
            detail="Failed to load Teamwork timelogs for this period.",
            start=start,
            end=end,
        )

    return build_reconciliation(
        entries,
        start=start,
        end=end,
        roster=roster,
        teamwork_people=tw_people,
        contractor_filter=contractor_filter,
    )
