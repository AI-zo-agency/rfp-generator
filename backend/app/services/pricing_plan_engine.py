"""Pricing plan v2 engine — pure code, no I/O.

The LLM authors a plan; this module is the only place money is computed.
Every figure derives from 00_Guide_Pricing bands and the Labor Cost Billable
column, and every check here is what the repair loop feeds back to the LLM.
"""

from __future__ import annotations

import re

VERBATIM_KEYS = ("investment_framing", "scope_protection", "reimbursables", "revisions")
BLOCK_TOKENS = {"TASK_TABLE", "STAFFING_TABLE", "RATE_TABLE", "FORM", "MEDIA_SPLIT", "VERBATIM"}
BILLING = ("one_time", "monthly", "per_event", "hourly")
BANNED = re.compile(
    r"(?i)00_guide|internal rate|raw floor|\bguide\b|\btier\b|sonja|\bella\b|labor cost card"
)
TOKEN_RE = re.compile(r"\{\{([A-Z_]+)(?::([^}]+))?\}\}")


# ---------------------------------------------------------------- KB parsing


def money(s: str) -> float:
    return float(s.replace(",", "").replace("$", ""))


def parse_guide(md: str) -> dict:
    heads = list(re.finditer(r"^\*\*(\d+\.\d+) (.+?)\*\*\s*$", md, re.M))
    items: dict[str, dict] = {}
    for i, h in enumerate(heads):
        chunk = md[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(md)]
        tiers = {
            t: (money(lo), money(hi))
            for t, lo, hi in re.findall(
                r"\|\s*\*\*(Low|Average|High)[^*]*\*\*\s*\|\s*\$([\d,]+)\s*to\s*\$([\d,]+)", chunk
            )
        }
        items[h.group(1)] = {"name": h.group(2).strip(), "tiers": tiers}
    blocks = re.findall(r"\*\*USE VERBATIM\*\*(.+?)\s*\|", md)
    verbatim = dict(zip(VERBATIM_KEYS, (b.strip() for b in blocks)))
    m = re.search(r"QUALIFYING LANGUAGE — MEDIA\*\*(.+?)\s*\|", md)
    if m:
        verbatim["media"] = m.group(1).strip()
    return {"items": items, "verbatim": verbatim}


def parse_labor(md: str) -> dict[str, float]:
    return {
        role.strip(): money(rate)
        for role, rate in re.findall(r"^\|\s*([A-Za-z][A-Za-z ]+?)\s*\|\s*\$([\d,.]+)", md, re.M)
    }


# ---------------------------------------------------------------- tier (code decides)


def decide_tier(asks: dict) -> tuple[str, str]:
    """Guide Decision Guide as rules over quoted facts."""
    f = asks.get("tier_facts") or {}
    cw = asks.get("cost_weight_pct")
    if cw is not None and float(cw) >= 25:
        return "Low", f"cost weighted {cw}% (>= 25%)"
    if (f.get("cost_primary_factor") or {}).get("value"):
        return "Low", "RFP states cost is the primary factor"
    scale = f.get("client_scale")
    complex_n = sum(bool((f.get(k) or {}).get("value")) for k in ("multicultural_or_bilingual", "multi_department", "statewide_or_regional"))
    if scale in {"state_agency", "large_county", "university"} and complex_n >= 2 and (cw is None or float(cw) <= 20):
        return "High", f"{scale}, {complex_n} complexity signals, cost weight {cw}"
    if scale in {"small_county", "nonprofit"} and complex_n == 0:
        return "Low", f"{scale}, straightforward scope"
    return "Average", f"default ({scale}, {complex_n} complexity signals, cost weight {cw})"


# ---------------------------------------------------------------- compute


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9$]+", " ", (s or "").lower()).strip()


def compute(plan: dict, labor: dict) -> dict:
    """Derive every money figure from the plan. The LLM's numbers are inputs, never outputs."""
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


# ---------------------------------------------------------------- verify


def verify_asks(asks: dict, rfp: str) -> list[str]:
    body = _norm(rfp)
    errs = []
    quoted = [("ask " + a.get("id", "?"), a.get("quote")) for a in asks.get("asks", [])]
    quoted += [("ceiling " + c.get("label", "?"), c.get("quote")) for c in asks.get("ceilings", [])]
    for k, v in (asks.get("tier_facts") or {}).items():
        if isinstance(v, dict) and v.get("value"):
            quoted.append((f"tier_facts.{k}", v.get("quote")))
    if not asks.get("priced_scope"):
        errs.append("priced_scope is empty — list every SOW deliverable")
    ai = asks.get("all_inclusive_pricing") or {}
    if ai.get("value"):
        quoted.append(("all_inclusive_pricing", ai.get("quote")))
    for label, q in quoted:
        if q and _norm(q)[:120] not in body:
            errs.append(f"{label}: quote not found verbatim in RFP")
    for c in asks.get("ceilings", []):
        amt = f"{int(float(c.get('amount') or 0)):,}"
        if amt not in rfp:
            errs.append(f"ceiling {c.get('label')}: {amt} not in RFP text")
    return errs


def verify_plan(
    plan: dict,
    asks: dict,
    guide: dict,
    labor: dict,
    *,
    target_budget_usd: float | None = None,
) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    items, tier = guide["items"], decide_tier(asks)[0]
    covered = {s for t in plan.get("tasks", []) for s in t.get("scope_ids") or []}
    missing_scope = [s["id"] for s in asks.get("priced_scope", []) if s.get("id") not in covered]
    if missing_scope:
        errs.append(f"SOW items with no task: {missing_scope}")
    c = compute(plan, labor)
    tracks = {x.get("track") for x in asks.get("ceilings", []) if x.get("track")}
    labels = tracks | {x.get("label") for x in asks.get("ceilings", [])}
    seen = set()
    for t in plan.get("tasks", []):
        tid, gid = t.get("task_id"), t.get("guide_id")
        if tid in seen:
            errs.append(f"{tid}: duplicate task_id")
        seen.add(tid)
        if t.get("billing", "one_time") not in BILLING:
            errs.append(f"{tid}: billing {t.get('billing')!r} invalid")
        if t.get("track") and tracks and t["track"] not in tracks:
            errs.append(f"{tid}: track {t['track']!r} is not a ceiling label {sorted(tracks)}")
        bad = [m.get("role") for m in t.get("staffing") or [] if m.get("role") not in labor]
        if bad:
            errs.append(f"{tid}: staffing roles not on Labor Cost card: {bad}")
        if tid not in c["amounts"]:
            if not t.get("manual_reason"):
                errs.append(f"{tid}: unpriced without manual_reason")
            continue
        if not gid or gid not in items:
            errs.append(f"{tid}: priced but guide_id {gid!r} is not a Guide line")
            continue
        band = items[gid]["tiers"].get(tier)
        if gid != "6.1" and band:
            per_unit = c["amounts"][tid] / float(t.get("quantity") or 1)
            if not band[0] <= per_unit <= band[1]:
                how = "staffing hours x rates" if tid in c["hours"] else "unit_price"
                errs.append(f"{tid}: per-unit {per_unit:,.0f} ({how}) outside {gid} {tier} band {band[0]:,.0f}-{band[1]:,.0f}")
    staffing_asked = any(
        a.get("required") and a.get("kind") in {"staffing_matrix", "hours_per_task"} for a in asks.get("asks", [])
    )
    if staffing_asked:
        for t in plan.get("tasks", []):
            if t.get("task_id") in c["amounts"] and t.get("guide_id") != "6.1" and not t.get("staffing"):
                errs.append(f"{t['task_id']}: RFP requires hours/staffing per task")
    for r in plan.get("hourly_roles", []):
        if r not in labor:
            errs.append(f"hourly role {r!r} not on Labor Cost card")
    # ceilings
    for ceil in asks.get("ceilings", []):
        amt = float(ceil.get("amount") or 0)
        tr = ceil.get("track") if ceil.get("track") in tracks and len(asks["ceilings"]) > 1 else None
        spent = term_value(c, tr)
        media = term_value(c, tr, "6.1")
        scoped = [t for t in c["tasks"].values() if tr is None or t.get("track") == tr]
        if any(t.get("billing") in {"per_event", "hourly"} for t in scoped):
            warns.append(f"per-event/hourly fees are not counted against {ceil.get('label')}")
        if spent > amt:
            errs.append(f"priced {spent:,.0f} exceeds ceiling {ceil.get('label')} {amt:,.0f}")
        elif not ceil.get("shared_pool") and amt:
            # Media is a pass-through assumption: it must not be what reaches the band.
            fees, room = spent - media, amt - media
            if room <= 0 or not 0.65 <= fees / room <= 0.85:
                pct = f"{fees / room:.0%}" if room > 0 else "n/a"
                errs.append(f"priced fees {fees:,.0f} are {pct} of {ceil.get('label')} excluding media — must be 65-85%")
    own_ceilings = [x for x in asks.get("ceilings", []) if not x.get("shared_pool")]
    if not own_ceilings:
        value = term_value(c)
        if target_budget_usd:
            if not 0.9 <= value / target_budget_usd <= 1.1:
                errs.append(
                    f"priced {value:,.0f} is {value / target_budget_usd:.0%} of the target budget "
                    f"{target_budget_usd:,.0f} — must be 90-110%"
                )
        else:
            warns.append("unanchored: no RFP ceiling and no target budget — total needs human review")
    notes = " ".join(n.get("issue", "") for n in plan.get("internal_notes", [])).lower()
    for lim in asks.get("implied_limits") or []:
        warns.append(f"implied limit: {lim.get('label')} — {lim.get('note')}")
        if not any(w in notes for w in _norm(lim.get("label", "")).split() if len(w) > 5):
            errs.append(f"implied limit {lim.get('label')!r} not raised in internal_notes")
    # form coverage
    form = asks.get("buyer_form")
    if form:
        for row in form.get("rows", []):
            for col in form.get("columns", []):
                if (row["row_id"], col) not in c["fills"]:
                    errs.append(f"form cell {row['row_id']}/{col} ({row['label']}) has no fill")
        for (rid, col), (k, v) in c["fills"].items():
            if k != "manual" and v is None:
                errs.append(f"form fill {rid}/{col} kind={k} computed nothing (tasks unpriced or no staffing hours)")
        # Tie-out: a track's form row must cover every priced task in that track.
        fill_tasks = {
            f.get("row_id"): set(f.get("task_ids") or [])
            for f in plan.get("form_fills", []) if f.get("kind") in {"extended", "task_price", "hours"}
        }
        for row in form.get("rows", []):
            tr = row.get("track")
            if not tr or row["row_id"] not in fill_tasks:
                continue
            priced = {t["task_id"] for t in plan.get("tasks", []) if t.get("track") == tr and t["task_id"] in c["amounts"]}
            if fill_tasks[row["row_id"]] != priced:
                errs.append(f"form row {row['row_id']} covers {sorted(fill_tasks[row['row_id']])} but track "
                            f"{tr!r} priced tasks are {sorted(priced)} — form and task table must tie out")
            mixed = {c["tasks"][i].get("billing", "one_time") for i in priced}
            if len(mixed) > 1:
                errs.append(f"track {tr!r} mixes billing {sorted(mixed)} under one form row — use one_time with quantity=months")
    # prose
    body = "\n".join(s.get("body_md", "") for s in plan.get("sections", []))
    found = [m.groups() for m in TOKEN_RE.finditer(body)]
    for name, arg in found:
        if name == "VERBATIM" and arg not in guide["verbatim"]:
            errs.append(f"unknown verbatim block {arg}")
        if name == "AMT" and arg not in c["amounts"]:
            errs.append(f"{{{{AMT:{arg}}}}} references an unpriced/unknown task")
        if name == "RATE" and arg not in labor:
            errs.append(f"{{{{RATE:{arg}}}}} role not on card")
        if name in {"CEILING", "UNALLOCATED", "TOTAL", "TASK_TABLE", "STAFFING_TABLE"} and arg and arg not in labels:
            errs.append(f"{{{{{name}:{arg}}}}} track not a ceiling label")
    blocks = [(n, a) for n, a in found if n in BLOCK_TOKENS]
    for dup in {b for b in blocks if blocks.count(b) > 1}:
        errs.append(f"block token {dup[0]}{':' + dup[1] if dup[1] else ''} used more than once")
    rendered_text = [("prose", TOKEN_RE.sub("", body))]
    rendered_text += [(f"section heading {s.get('heading')!r}", s.get("heading") or "") for s in plan.get("sections", [])]
    for t in plan.get("tasks", []):
        rendered_text += [(f"{t.get('task_id')} {k}", t.get(k) or "") for k in ("deliverable", "group")]
    for label, text in rendered_text:
        if re.search(r"\$\s?\d", text):
            errs.append(f"{label} contains a literal dollar figure" + (" — use tokens" if label == "prose" else ""))
        hit = BANNED.search(text)
        if hit:
            errs.append(f"{label} contains internal jargon: {hit.group(0)!r}")
    all_in = bool((asks.get("all_inclusive_pricing") or {}).get("value"))
    required = [k for k in VERBATIM_KEYS if not (all_in and k == "reimbursables")]
    for k in required:
        if f"{{{{VERBATIM:{k}}}}}" not in body:
            errs.append(f"missing {{{{VERBATIM:{k}}}}}")
    if all_in and "{{VERBATIM:reimbursables}}" in body:
        errs.append("RFP requires all-inclusive pricing — remove {{VERBATIM:reimbursables}}")
    if any(t.get("guide_id") == "6.1" for t in plan.get("tasks", [])) and "{{VERBATIM:media}}" not in body:
        errs.append("6.1 media used but {{VERBATIM:media}} missing")
    used = {n for n, _ in found}
    need = {
        "cost_per_task": ("TASK_TABLE",), "cost_per_deliverable": ("TASK_TABLE",), "itemized_budget": ("TASK_TABLE",),
        "hourly_rates": ("RATE_TABLE", "FORM"), "staffing_matrix": ("STAFFING_TABLE",),
        "hours_per_task": ("STAFFING_TABLE", "FORM"),
    }
    for a in asks.get("asks", []):
        want = need.get(a.get("kind"))
        if a.get("required") and want and not used & set(want):
            errs.append(f"required ask {a['id']} ({a['kind']}) not answered: needs {' or '.join(want)}")
    if form and "FORM" not in used:
        errs.append("buyer form present but {{FORM}} not placed")
    outline = (asks.get("compensation_outline") or {}).get("items") or []
    if outline and len(plan.get("sections", [])) < len(outline):
        warns.append(f"RFP compensation outline has {len(outline)} items; plan has {len(plan.get('sections', []))} sections")
    return errs, warns


# ---------------------------------------------------------------- render


def usd(v, cents: bool = False) -> str:
    if v is None:
        return "[MANUAL FILL]"
    return f"${v:,.2f}" if cents and v != int(v) else f"${v:,.0f}"


SUFFIX = {"monthly": " / month", "per_event": " / event", "hourly": " / hour", "one_time": ""}


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
        "TOTAL": lambda a=None: usd(c["total"](a)),
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


def render_internal_notes(plan: dict) -> str:
    """Internal-only block (tier, owners, assumptions). Never part of the client Cost section."""
    out = ["**INTERNAL — DO NOT PLACE**", "", f"Tier: **{plan.get('tier')}** — {plan.get('tier_basis', '')}", ""]
    out += ["| # | Issue | Owner |", "|---|---|---|"]
    out += [f"| {i} | {n.get('issue', '')} | {n.get('owner', '')} |" for i, n in enumerate(plan.get("internal_notes", []), 1)]
    return "\n".join(out) + "\n"
