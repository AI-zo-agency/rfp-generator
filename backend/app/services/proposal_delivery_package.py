"""RFP-agnostic Delivery Package + role routing for substance / calendar / price.

Buyer tabs keep their RFP titles. Roles are assigned by Phase 2 planners (LLM
judgment by meaning) onto outline / section briefs as `deliveryRoles` — never by
expanding title synonym / regex tables.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable


class DeliveryRole(str, Enum):
    SUBSTANCE = "substance"  # what we do
    CALENDAR = "calendar"  # when
    PRICE = "price"  # dollars


_VALID = {r.value for r in DeliveryRole}


def normalize_delivery_roles(raw: Any) -> frozenset[DeliveryRole]:
    """Parse planner-emitted deliveryRoles (list/str) into a role set."""
    if raw is None:
        return frozenset()
    items: list[str] = []
    if isinstance(raw, str):
        items = [p.strip() for p in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        for item in raw:
            if isinstance(item, DeliveryRole):
                items.append(item.value)
            else:
                items.append(str(item).strip())
    else:
        return frozenset()
    roles: set[DeliveryRole] = set()
    for item in items:
        key = item.lower().strip()
        if key in _VALID:
            roles.add(DeliveryRole(key))
    return frozenset(roles)


def methodology_retrieval_query(
    *,
    project_type: str = "",
    industry: str = "",
    org_type: str = "",
    sector: str = "",
) -> str:
    """Sector-agnostic methodology retrieval — never force 'website' phases."""
    bits = [
        (project_type or "").strip(),
        (industry or "").strip(),
        (org_type or "").strip(),
        (sector or "").strip(),
        "delivery workstreams phases methodology",
    ]
    return " ".join(b for b in bits if b)


def build_delivery_package(execution_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize methodology + timeline into one package dict."""
    plan = execution_plan if isinstance(execution_plan, dict) else {}
    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), dict) else {}
    methodology = (
        delivery.get("methodology") if isinstance(delivery.get("methodology"), dict) else {}
    )
    timeline = delivery.get("timeline") if isinstance(delivery.get("timeline"), dict) else {}

    workstreams: list[dict[str, Any]] = []
    for phase in methodology.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        name = str(phase.get("name") or "").strip()
        if not name:
            continue
        activities = phase.get("activities") or []
        if not isinstance(activities, list):
            activities = []
        workstreams.append(
            {
                "name": name,
                "activities": [str(a).strip() for a in activities if str(a).strip()],
                "governance": str(phase.get("governance") or "").strip(),
            }
        )

    milestones: list[dict[str, Any]] = []
    for ms in timeline.get("milestones") or []:
        if not isinstance(ms, dict):
            continue
        name = str(ms.get("name") or "").strip()
        if not name:
            continue
        depends = ms.get("dependsOn") or ms.get("depends_on") or []
        if not isinstance(depends, list):
            depends = []
        milestones.append(
            {
                "name": name,
                "offset": str(ms.get("offset") or "").strip(),
                "dependsOn": [str(d).strip() for d in depends if str(d).strip()],
            }
        )

    return {
        "workstreams": workstreams,
        "milestones": milestones,
        "goLive": str(timeline.get("goLive") or timeline.get("go_live") or "").strip(),
        "reviewCycles": str(
            timeline.get("reviewCycles") or timeline.get("review_cycles") or ""
        ).strip(),
        "methodologyConfidence": methodology.get("confidence"),
        "timelineConfidence": timeline.get("confidence"),
    }


def workstream_names(package: dict[str, Any] | None) -> list[str]:
    pkg = package if isinstance(package, dict) else {}
    names: list[str] = []
    for ws in pkg.get("workstreams") or []:
        if isinstance(ws, dict):
            name = str(ws.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def roles_from_execution_plan(
    execution_plan: dict[str, Any] | None,
    *,
    section_id: str = "",
    section_title: str = "",
) -> frozenset[DeliveryRole]:
    """Read LLM-assigned deliveryRoles from outline or section briefs.

    Never infers roles from title keywords — missing roles means no package
    injection for that tab (safer than a wrong synonym match).
    """
    plan = execution_plan if isinstance(execution_plan, dict) else {}
    writing = plan.get("writing") if isinstance(plan.get("writing"), dict) else {}
    sid = (section_id or "").strip()
    title = (section_title or "").strip()

    # Prefer section strategy briefs (closer to the writer).
    for brief in writing.get("sectionPlans", {}).get("plans") or writing.get(
        "section_plans", {}
    ).get("plans") or []:
        if not isinstance(brief, dict):
            continue
        brief_id = str(brief.get("sectionId") or brief.get("section_id") or "").strip()
        brief_title = str(brief.get("title") or "").strip()
        if sid and brief_id == sid:
            roles = normalize_delivery_roles(
                brief.get("deliveryRoles") or brief.get("delivery_roles")
            )
            if roles:
                return roles
        if title and brief_title and brief_title.casefold() == title.casefold():
            roles = normalize_delivery_roles(
                brief.get("deliveryRoles") or brief.get("delivery_roles")
            )
            if roles:
                return roles

    # Fall back to outline stamps from dynamic section planner.
    outline = writing.get("proposalOutline") or writing.get("proposal_outline") or {}
    sections = outline.get("sections") if isinstance(outline, dict) else None
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec_id = str(sec.get("id") or "").strip()
            sec_title = str(sec.get("title") or "").strip()
            if sid and sec_id == sid:
                roles = normalize_delivery_roles(
                    sec.get("deliveryRoles") or sec.get("delivery_roles")
                )
                if roles:
                    return roles
            if title and sec_title and sec_title.casefold() == title.casefold():
                roles = normalize_delivery_roles(
                    sec.get("deliveryRoles") or sec.get("delivery_roles")
                )
                if roles:
                    return roles

    return frozenset()


def format_delivery_package_block(
    roles: frozenset[DeliveryRole] | set[DeliveryRole] | Iterable[DeliveryRole],
    package: dict[str, Any] | None,
    *,
    section_title: str = "",
) -> str:
    """Additive prompt block for a section that owns one or more delivery roles."""
    role_set = frozenset(roles) if not isinstance(roles, frozenset) else roles
    if not role_set:
        return ""
    pkg = package if isinstance(package, dict) else {}
    workstreams = pkg.get("workstreams") if isinstance(pkg.get("workstreams"), list) else []
    milestones = pkg.get("milestones") if isinstance(pkg.get("milestones"), list) else []
    if not workstreams and not milestones and DeliveryRole.PRICE not in role_set:
        if DeliveryRole.SUBSTANCE not in role_set and DeliveryRole.CALENDAR not in role_set:
            return ""

    title = (section_title or "this section").strip() or "this section"
    lines: list[str] = [
        f"DELIVERY PACKAGE for tab «{title}» (buyer title stays exact — do not rename the tab).",
        "Roles were assigned by meaning for THIS RFP (not by title keywords).",
        "Use this internal package so substance, calendar, and price stay name-aligned.",
        "Never invent Zo section titles. Never paraphrase the RFP ask list into the body.",
    ]

    if workstreams:
        lines.append("Workstreams (canonical names — reuse verbatim across roles):")
        for ws in workstreams:
            if not isinstance(ws, dict):
                continue
            name = str(ws.get("name") or "").strip()
            if not name:
                continue
            acts = ", ".join(str(a) for a in (ws.get("activities") or [])[:6] if str(a).strip())
            gov = str(ws.get("governance") or "").strip()
            detail = name
            if acts:
                detail += f" — {acts}"
            if gov:
                detail += f" (governance: {gov})"
            lines.append(f"- {detail}")

    if milestones:
        lines.append("Calendar anchors:")
        for ms in milestones:
            if not isinstance(ms, dict):
                continue
            name = str(ms.get("name") or "").strip()
            if not name:
                continue
            offset = str(ms.get("offset") or "").strip() or "Timing TBD from RFP window"
            lines.append(f"- {name}: {offset}")
    go_live = str(pkg.get("goLive") or "").strip()
    reviews = str(pkg.get("reviewCycles") or "").strip()
    if go_live:
        lines.append(f"Go-live / peak: {go_live}")
    if reviews:
        lines.append(f"Review cadence: {reviews}")

    if DeliveryRole.SUBSTANCE in role_set:
        lines.extend(
            [
                "SUBSTANCE ROLE (this tab owns the plan):",
                "Write one complete defendable plan for THIS RFP — not a partial angle.",
                "Required shape: (1) short operating thesis, (2) 4–6 named workstreams "
                "with what we will do, (3) checkpoints / governance, (4) explicit out-of-scope.",
                "Prefer the canonical workstream names above when present. "
                "If this tab also owns calendar, include a Timing table using the same names.",
                "Sector-agnostic: do not force website UX/Dev phases unless this RFP is a build.",
            ]
        )
    if DeliveryRole.CALENDAR in role_set:
        lines.extend(
            [
                "CALENDAR ROLE (this tab owns timing):",
                "Dates / milestones / owners only — do not restate full methodology prose.",
                "Every Timing cell must use week-from-award or calendar month from the RFP window.",
                "Bind rows to the same workstream names as substance (no parallel renamed phases).",
                "Missing RFP dates → week-from-award from award; use [VERIFY] only when the "
                "RFP gives no window and no event dates.",
            ]
        )
    if DeliveryRole.PRICE in role_set:
        lines.extend(
            [
                "PRICE ROLE (this tab owns dollars — Phase 3 narrative only here):",
                "Transparency / model / allocation rationale only. Do not invent fee tables "
                "(Phase 3.5 owns dollars from the rate guide).",
                "Every future fee line must map to a canonical workstream name above — "
                "do not invent an alternate phase taxonomy.",
            ]
        )

    lines.append("END DELIVERY PACKAGE")
    return "\n".join(lines)
