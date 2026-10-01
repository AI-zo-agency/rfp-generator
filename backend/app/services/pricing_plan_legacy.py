"""Frozen renderer for pricing plans saved before pricing methodology v2 (band-based plans).

Proposals priced with the old Pricing Guide keep rendering exactly as they did.
New budgets use pricing_plan_engine. Nothing here parses documents or prices work;
it only recomputes and renders a saved plan from its own kb_snapshot.
"""

from __future__ import annotations

import re

BLOCK_TOKENS = {"TASK_TABLE", "STAFFING_TABLE", "RATE_TABLE", "FORM", "MEDIA_SPLIT", "VERBATIM"}
TOKEN_RE = re.compile(r"\{\{([A-Z_]+)(?::([^}]+))?\}\}")
SUFFIX = {"monthly": " / month", "per_event": " / event", "hourly": " / hour", "one_time": ""}


def is_legacy(plan: dict) -> bool:
    """True for a plan saved by the band-based engine (its snapshot carries labor rates, not settings)."""
    return "settings" not in (plan.get("kb_snapshot") or {})


def usd(v, cents: bool = False) -> str:
    if v is None:
        return "[MANUAL FILL]"
    return f"${v:,.2f}" if cents and v != int(v) else f"${v:,.0f}"


def compute(plan: dict, labor: dict) -> dict:
    amounts, hours = {}, {}
    for t in plan.get("tasks", []):
        mix = t.get("staffing") or []
        if mix and all(m.get("role") in labor for m in mix):
            hours[t["task_id"]] = sum(float(m["hours"]) for m in mix)
            amounts[t["task_id"]] = round(sum(labor[m["role"]] * float(m["hours"]) for m in mix))
        elif t.get("unit_price") is not None:
            amounts[t["task_id"]] = round(float(t["unit_price"]) * float(t.get("quantity") or 1))
    tasks = {t["task_id"]: t for t in plan.get("tasks", [])}

    def total(track=None, billing="one_time", guide_id=None):
        if track is not None and not any(t.get("track") == track for t in tasks.values()):
            track = None  # single-ceiling label, not a track
        return sum(
            a for tid, a in amounts.items()
            if tasks[tid].get("billing", "one_time") == billing
            and (track is None or tasks[tid].get("track") == track)
            and (guide_id is None or tasks[tid].get("guide_id") == guide_id)
        )

    fills = {}
    for f in plan.get("form_fills", []):
        ids, k = f.get("task_ids") or [], f.get("kind")
        price = sum(amounts.get(i, 0) for i in ids)
        hrs = sum(hours.get(i, 0) for i in ids)
        rate = round(price / hrs, 2) if hrs else None
        val = {
            "task_price": price if ids else None,
            "hours": hrs or None,
            "rate": rate,
            "extended": round(rate * hrs, 2) if rate else None,
            "labor_rate": labor.get(f.get("role") or ""),
        }.get(k)
        fills[(f.get("row_id"), f.get("column"))] = (k, val)
    return {"amounts": amounts, "hours": hours, "tasks": tasks, "total": total, "fills": fills}


def term_value(c: dict, track=None, guide_id=None) -> float:
    """One-time work plus a year of monthly fees. per_event / hourly are rates, never summed."""
    return c["total"](track, "one_time", guide_id) + 12 * c["total"](track, "monthly", guide_id)


def render(plan: dict, asks: dict, guide: dict, labor: dict) -> str:
    c = compute(plan, labor)
    tasks = plan.get("tasks", [])
    ceils = {x.get("track") or "__total__": x for x in asks.get("ceilings", [])}
    ceils.update({x["label"]: x for x in asks.get("ceilings", []) if x.get("label")})
    if len(asks.get("ceilings", [])) == 1:
        ceils["__total__"] = asks["ceilings"][0]

    def ceiling(track=None):
        x = ceils.get(track or "__total__")
        return None if not x or x.get("shared_pool") else float(x["amount"])

    def task_table(track=None) -> str:
        rows = ["| Task ID | Task / Deliverable | Investment |", "|---|---|---:|"]
        scoped = [t for t in tasks if track is None or t.get("track") == track]
        groups = list(dict.fromkeys(t.get("group") for t in scoped))  # first-appearance order
        for group in groups:
            rows.append(f"| | **{group}** | |")
            for t in (t for t in scoped if t.get("group") == group):
                amt = c["amounts"].get(t["task_id"])
                rows.append(f"| {t['task_id']} | {t['deliverable']} | {usd(amt)}{SUFFIX[t.get('billing', 'one_time')] if amt else ''} |")
        billings = {t.get("billing", "one_time") for t in scoped}
        if billings & {"one_time", "monthly"}:
            value = term_value(c, track)
            label = "Priced work (term value)" if "monthly" in billings else "Priced tasks"
            rows.append(f"| | **{label}** | **{usd(value)}** |")
            cap = ceiling(track)
            if cap:
                unpriced = [t["task_id"] for t in scoped if t["task_id"] not in c["amounts"]]
                label = "Held for scope confirmed on approval" + (f" (incl. {', '.join(unpriced)})" if unpriced else "")
                rows.append(f"| | {label} | {usd(cap - value)} |")
                rows.append(f"| | **Total not-to-exceed** | **{usd(cap)}** |")
        return "\n".join(rows)

    def staffing_table(track=None) -> str:
        rows = ["| Task ID | Role | Hours | Rate | Cost |", "|---|---|---:|---:|---:|"]
        for t in tasks:
            if track is not None and t.get("track") != track:
                continue
            for m in t.get("staffing") or []:
                r = labor.get(m["role"])
                rows.append(f"| {t['task_id']} | {m['role']} | {float(m['hours']):g} | {usd(r)} | {usd(r * float(m['hours']) if r else None)} |")
        return "\n".join(rows)

    def rate_table(_=None) -> str:
        return "\n".join(["| Role | Hourly Rate |", "|---|---:|"] + [f"| {r} | {usd(labor.get(r))} |" for r in plan.get("hourly_roles", [])])

    def form(_=None) -> str:
        f = asks.get("buyer_form") or {}
        cols = f.get("columns") or ["Value"]
        rows = [f"**{f.get('name', 'Pricing Form')}**", "", "| Item | UOM | " + " | ".join(cols) + " |", "|---|---|" + "---:|" * len(cols)]
        for r in f.get("rows", []):
            cells = []
            for col in cols:
                k, v = c["fills"].get((r["row_id"], col), (None, None))
                cells.append("" if k is None else (f"{v:g}" if k == "hours" and v else usd(v, cents=True)))
            rows.append(f"| {r['label']} | {r.get('unit', '')} | " + " | ".join(cells) + " |")
        return "\n".join(rows)

    def media_split(_=None) -> str:
        rows = ["| Media | Total | Placements (85%) | Agency (15%) |", "|---|---:|---:|---:|"]
        for t in tasks:
            if t.get("guide_id") == "6.1":
                amt = c["amounts"].get(t["task_id"], 0)
                rows.append(f"| {t['task_id']} {t['deliverable']} | {usd(amt)} | {usd(amt * 0.85)} | {usd(amt * 0.15)} |")
        return "\n".join(rows)

    def unallocated(track=None):
        cap = ceiling(track)
        return usd(cap - term_value(c, track) if cap else None)

    table = {
        "TASK_TABLE": task_table, "STAFFING_TABLE": staffing_table, "RATE_TABLE": rate_table,
        "FORM": form, "MEDIA_SPLIT": media_split,
        "TOTAL": lambda a=None: usd(term_value(c, a)),
        "CEILING": lambda a=None: usd(ceiling(a)),
        "UNALLOCATED": unallocated,
        "AMT": lambda a: usd(c["amounts"].get(a)),
        "RATE": lambda a: usd(labor.get(a)),
        "VERBATIM": lambda a: guide["verbatim"].get(a, ""),
    }

    def sub(m: re.Match) -> str:
        name, arg = m.groups()
        if name not in table:
            return m.group(0)
        val = table[name](arg) if arg else table[name]()
        return f"\n\n{val}\n\n" if name in BLOCK_TOKENS else val

    out = []
    for s in plan.get("sections", []):
        body = re.sub(r"\n{3,}", "\n\n", TOKEN_RE.sub(sub, s.get("body_md", ""))).strip()
        out += [f"## {s['heading']}", "", body, ""]
    return "\n".join(out).rstrip() + "\n"
