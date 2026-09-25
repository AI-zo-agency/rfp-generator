"""Prototype budget rebuild: asks -> author -> verify/repair -> render.

No format templates. The LLM authors the section as a structured plan plus prose;
prose may not contain dollar figures (money only via {{TOKENS}}), and code derives
and checks every number against 00_Guide_Pricing tier bands and the Labor Cost card.

Usage (from backend/):
  PYTHONPATH=. ../.venv/bin/python scripts/budget_rebuild_proto.py --fetch-kb OUT
  PYTHONPATH=. ../.venv/bin/python scripts/budget_rebuild_proto.py OUT rfp1.txt rfp2.txt ...
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from app.services import llm

MODEL_KW = dict(tier="heavy", node_name="phase-3-5-budget-proto", include_corrections=False)
VERBATIM_KEYS = ("investment_framing", "scope_protection", "reimbursables", "revisions")
BLOCK_TOKENS = {"TASK_TABLE", "STAFFING_TABLE", "RATE_TABLE", "FORM", "MEDIA_SPLIT", "VERBATIM"}
BILLING = ("one_time", "monthly", "per_event", "hourly")
BANNED = re.compile(
    r"(?i)00_guide|internal rate|raw floor|\bguide\b|\btier\b|sonja|\bella\b|labor cost card"
)
RUN_TAG = ""
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


# ---------------------------------------------------------------- prompts

ASKS_PROMPT = """You read a public-sector RFP and list how the buyer wants the COST / PRICE / BUDGET submitted.
Return JSON only:
{
 "compensation_outline": {"ref": "RFP section number + title of the cost/compensation section, or null",
                          "items": ["the RFP's own numbered items in that section, short, with their numbers"]},
 "ceilings": [{"label": "...", "amount": 950000, "scope": "total|annual|track", "track": "track name or null",
               "shared_pool": false, "quote": "verbatim"}],
 "implied_limits": [{"label": "...", "note": "e.g. procurement method statutorily capped", "quote": "verbatim"}],
 "cost_weight_pct": null or number (cost points / total points * 100),
 "cost_weight_quote": "verbatim or null",
 "all_inclusive_pricing": {"value": false, "quote": "verbatim or null"},
 "buyer_form": null or {"name": "...", "may_modify": false,
     "columns": ["only the columns the bidder fills, e.g. QTY, PRICE, EXTENDED PRICE or one per department"],
     "rows": [{"row_id": "R1", "label": "exact row label", "unit": "HR|month|EA|...", "track": "track or null"}],
     "quote": "verbatim instruction"},
 "asks": [{"id": "K1", "kind": "cost_per_task|cost_per_deliverable|hourly_rates|hours_per_task|staffing_matrix|monthly_fee|unit_price|lump_sum|itemized_budget|invoice_content|payment_terms|media_costs|reimbursables|rate_lock|other",
           "required": true, "quote": "verbatim, <= 40 words", "locator": "section/page"}],
 "priced_scope": [{"id": "S1", "ref": "SOW ref", "item": "work item / deliverable the buyer names", "track": "track or null"}],
 "tier_facts": {
   "client_scale": "state_agency|large_county|university|city|small_county|nonprofit|other",
   "multicultural_or_bilingual": {"value": false, "quote": "verbatim or null"},
   "multi_department": {"value": false, "quote": "verbatim or null"},
   "statewide_or_regional": {"value": false, "quote": "verbatim or null"},
   "cost_primary_factor": {"value": false, "quote": "verbatim or null"}},
 "exclusions": ["e.g. capital costs not allowed"]
}
Rules:
- Quotes MUST be copied verbatim from the RFP (they are machine-checked).
- An "e.g." list of alternative pricing methods is a menu: each kind is required=false.
  required=true only when the RFP says shall/must/provide for that specific method or a form row demands it.
- shared_pool=true when the amount is shared across multiple awards/contracts (not one bidder's ceiling).
- all_inclusive_pricing=true when prices must include travel/expenses/materials (no separate reimbursables).
- If a cost form is referenced but not included in the text, set buyer_form=null and add an ask kind "other" saying so.
- priced_scope: list EVERY deliverable / work item in the scope of work (be complete; 5-25 items typical).
- tier_facts: true only with a verbatim quote proving it.
- Do not invent numbers."""

AUTHOR_PROMPT = """You are zö agency's pricing lead writing the budget / cost section of a proposal.
Sources: the RFP, the extracted pricing ASKS, the Pricing Guide (menu with Low/Average/High bands),
and the Labor Cost card (billable $/hr by role).

Return JSON only:
{
 "tier_rationale": "one line: how the given tier shapes the pricing",
 "tasks": [{"task_id": "A1", "group": "short group heading", "deliverable": "...",
            "scope_ids": ["S1"], "track": "exact ceiling/track label from ASKS or null",
            "billing": "one_time|monthly|per_event|hourly",
            "guide_id": "1.1 or null", "quantity": 1, "unit_price": 12000 or null,
            "manual_reason": null or "why no Guide line prices this",
            "staffing": [{"role": "exact Labor Cost role", "hours": 10}] or []}],
 "hourly_roles": ["exact Labor Cost role names for a rate table (only if rates are asked or useful)"],
 "form_fills": [{"row_id": "R1", "column": "exact column from ASKS buyer_form", "kind": "task_price|hours|rate|extended|labor_rate|manual",
                 "task_ids": ["..."], "role": "for labor_rate", "note": "..."}],
 "sections": [{"heading": "...", "body_md": "..."}],
 "internal_notes": [{"issue": "...", "owner": "Sonja|Ella|Writer"}]
}

PRICING RULES
- The TIER is decided by code and given to you below. Price every line inside that tier's band.
- Every ASKS priced_scope id must appear in some task's scope_ids (priced, or manual with a reason).
- Priced task: guide_id set; the per-unit price must sit INSIDE that line's band for the chosen tier;
  quantity = count of units (assets, months, campaigns, events). Per-asset and monthly lines are per unit.
- billing: one_time = total over the term (quantity x unit); monthly = a recurring monthly fee (quantity 1,
  shown per month); per_event = price per occurrence; hourly = rate only.
- Staffing: when the RFP asks hours / staff / roles per task, or a buyer form asks hours or an hourly rate
  built from the work, give priced tasks a staffing mix and set unit_price null. Code then prices the task as
  sum(hours x card rate) and checks that per-unit value is inside the Guide band. Roles must be exact card names.
  Without staffing, set unit_price yourself.
- Guide 6.1 media: guide_id "6.1", unit_price = total media budget, no staffing (code computes 85/15).
- Scope no Guide line covers: guide_id null, unit_price null, staffing [], manual_reason set. Never invent a price.
- A ceiling that is ours alone: priced one_time tasks must total 65-85% of it (leave 15-20% for expansion,
  but do not leave the buyer's budget mostly unused). Adjust quantities and scope depth to land there.
  shared_pool ceilings are not ours: size the bid to the scope and do not claim the pool as our NTE.
- Priced one_time tasks per track must stay <= that track's ceiling.
- Hourly rates come ONLY from the Labor Cost card. You never type a rate or a dollar figure.
- Buyer form fill kinds (code computes values):
  task_price = sum of the listed tasks' price (monthly fee, event fee, lump sum);
  hours = sum of staffing hours of the listed tasks; rate = task_price / hours (effective hourly rate);
  extended = rate x hours; labor_rate = one card role's rate; manual = leave blank with a note.
  One fill per form row x bidder column. Keep NO./ITEM/UOM columns out.

RFP OVERRIDES THE GUIDE
- If ASKS all_inclusive_pricing is true, do NOT use {{VERBATIM:reimbursables}}; say prices are all-inclusive.
- Honor rate locks, exclusions (e.g. no capital costs), invoice content and payment terms stated in the RFP.

WRITING RULES
- When ASKS compensation_outline has items, the section headings MUST mirror that numbering and order
  (e.g. "1. Compensation Contingent on Approved Deliverables"); answer each item under its own number.
  Otherwise use: Investment Summary, Cost by Task, Rates (if asked), Media (if any), Terms.
- body_md contains NO dollar figures and NO money percentages. Money appears only through tokens:
  block tokens (own line, each used at most once per argument):
    {{TASK_TABLE}} or {{TASK_TABLE:<track>}}  {{STAFFING_TABLE}} or {{STAFFING_TABLE:<track>}}
    {{RATE_TABLE}}  {{FORM}}  {{MEDIA_SPLIT}}  {{VERBATIM:<key>}}
  inline tokens: {{TOTAL}} {{TOTAL:<track>}} {{CEILING:<track>}} {{UNALLOCATED:<track>}} {{AMT:<task_id>}} {{RATE:<role>}}
  <track> is the exact track/ceiling label. {{TOTAL}} = all one_time priced tasks.
- Verbatim blocks: {{VERBATIM:investment_framing}} {{VERBATIM:scope_protection}} {{VERBATIM:revisions}},
  {{VERBATIM:reimbursables}} (unless all-inclusive), {{VERBATIM:media}} when 6.1 is used.
- Answer every required ask explicitly (invoice content, payment terms, rate lock, exclusions...).
- Client-facing prose: no internal names (Sonja, Ella), no "Guide", "tier", "Internal Rate", "Raw floor".
  Put open questions, assumptions (quantities, hours, media budgets), and approvals in internal_notes.
- Every ASKS implied_limits entry must be raised in internal_notes with what it means for the total.
- Short, plain, confident sentences. No filler."""


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

    def total(track=None, billing="one_time"):
        if track is not None and not any(t.get("track") == track for t in tasks.values()):
            track = None  # single-ceiling label, not a track
        return sum(
            a for tid, a in amounts.items()
            if tasks[tid].get("billing", "one_time") == billing and (track is None or tasks[tid].get("track") == track)
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


def verify_plan(plan: dict, asks: dict, guide: dict, labor: dict) -> tuple[list[str], list[str]]:
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
    for r in plan.get("hourly_roles", []):
        if r not in labor:
            errs.append(f"hourly role {r!r} not on Labor Cost card")
    # ceilings
    for ceil in asks.get("ceilings", []):
        amt = float(ceil.get("amount") or 0)
        spent = c["total"](ceil.get("track") if ceil.get("track") in tracks and len(asks["ceilings"]) > 1 else None)
        if spent > amt:
            errs.append(f"priced {spent:,.0f} exceeds ceiling {ceil.get('label')} {amt:,.0f}")
        elif not ceil.get("shared_pool") and amt and not 0.65 <= spent / amt <= 0.85:
            errs.append(f"priced {spent:,.0f} is {spent / amt:.0%} of {ceil.get('label')} — must be 65-85%")
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
    stripped = TOKEN_RE.sub("", body)
    if re.search(r"\$\s?\d", stripped):
        errs.append("prose contains a literal dollar figure — use tokens")
    hit = BANNED.search(stripped)
    if hit:
        errs.append(f"prose contains internal jargon: {hit.group(0)!r}")
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
        group = None
        scoped = [t for t in tasks if track is None or t.get("track") == track]
        for t in scoped:
            if t.get("group") != group:
                group = t.get("group")
                rows.append(f"| | **{group}** | |")
            amt = c["amounts"].get(t["task_id"])
            rows.append(f"| {t['task_id']} | {t['deliverable']} | {usd(amt)}{SUFFIX[t.get('billing', 'one_time')] if amt else ''} |")
        if any(t.get("billing", "one_time") == "one_time" for t in scoped):
            one = c["total"](track)
            rows.append(f"| | **Priced tasks** | **{usd(one)}** |")
            cap = ceiling(track)
            if cap:
                unpriced = [t["task_id"] for t in scoped if t["task_id"] not in c["amounts"]]
                label = "Held for scope confirmed on approval" + (f" (incl. {', '.join(unpriced)})" if unpriced else "")
                rows.append(f"| | {label} | {usd(cap - one)} |")
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
        return usd(cap - c["total"](track) if cap else None)

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
    out += ["---", "", "**INTERNAL — DO NOT PLACE**", "", f"Tier: **{plan.get('tier')}** — {plan.get('tier_rationale', '')}", ""]
    out += ["| # | Issue | Owner |", "|---|---|---|"]
    out += [f"| {i} | {n.get('issue', '')} | {n.get('owner', '')} |" for i, n in enumerate(plan.get("internal_notes", []), 1)]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- pipeline


SPEND_STOP_USD = 44.5  # user limit: stay under $45 monthly spend


async def call(system: str, user: str, max_tokens: int = 24000) -> dict:
    from app.services.monthly_llm_budget import get_monthly_budget_status

    spent = float(get_monthly_budget_status(use_cache=False).get("spent_usd") or 0)
    if spent >= SPEND_STOP_USD:
        raise RuntimeError(f"spend guard: ${spent:.2f} >= ${SPEND_STOP_USD}")
    raw, _ = await llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens, temperature=0.2, reasoning_effort="medium", **MODEL_KW,
    )
    return raw


def guide_menu(guide: dict) -> str:
    return "\n".join(
        f"{gid} {it['name']}: " + "; ".join(f"{t} {lo:,.0f}-{hi:,.0f}" for t, (lo, hi) in it["tiers"].items())
        for gid, it in guide["items"].items()
    )


async def run_one(rfp_path: Path, out: Path, guide_md: str, guide: dict, labor_md: str, labor: dict) -> dict:
    name, rfp = rfp_path.stem + RUN_TAG, rfp_path.read_text()
    log: list[str] = []
    asks = await call(ASKS_PROMPT, f"=== RFP ===\n{rfp}", 10000)
    ask_errs = verify_asks(asks, rfp)
    if ask_errs:
        log.append("asks check: " + "; ".join(ask_errs))
        asks = await call(ASKS_PROMPT, f"=== RFP ===\n{rfp}\n\n=== YOUR PREVIOUS JSON ===\n{json.dumps(asks)}\n\n"
                          "=== FIX THESE (quote exactly or drop the item) ===\n" + "\n".join(ask_errs), 10000)
        ask_errs = verify_asks(asks, rfp)
        log.append("asks recheck: " + ("ok" if not ask_errs else "; ".join(ask_errs)))
    (out / f"{name}.asks.json").write_text(json.dumps(asks, indent=2))

    tier, why = decide_tier(asks)
    ctx = (f"=== TIER (decided by code): {tier} — {why} ===\n\n"
           f"=== PRICING ASKS (extracted, verified) ===\n{json.dumps(asks, indent=1)}\n\n"
           f"=== PRICING GUIDE MENU (parsed bands) ===\n{guide_menu(guide)}\n\n"
           f"=== PRICING GUIDE (full) ===\n{guide_md}\n\n=== LABOR COST CARD ===\n{labor_md}\n\n=== RFP ===\n{rfp}")
    plan = await call(AUTHOR_PROMPT, ctx)
    rounds = []
    for attempt in range(3):
        errs, warns = verify_plan(plan, asks, guide, labor)
        rounds.append({"round": attempt, "errors": errs, "warnings": warns})
        if not errs or attempt == 2:
            break
        plan = await call(AUTHOR_PROMPT, ctx + f"\n\n=== YOUR PREVIOUS PLAN ===\n{json.dumps(plan)}\n\n"
                          "=== VERIFIER ERRORS — return the full corrected plan ===\n" + "\n".join(errs))
    plan["tier"] = tier
    plan["tier_rationale"] = f"{why}. {plan.get('tier_rationale', '')}"
    (out / f"{name}.plan.json").write_text(json.dumps(plan, indent=2))
    (out / f"{name}.budget.md").write_text(render(plan, asks, guide, labor))
    report = {"rfp": name, "log": log, "rounds": rounds, "total": compute(plan, labor)["total"](),
              "tier": tier, "tasks": len(plan.get("tasks", []))}
    (out / f"{name}.report.json").write_text(json.dumps(report, indent=2))
    return report


async def fetch_kb(out: Path) -> None:
    from app.services.proposal_pricing_service import _fetch_pinned_labor_rate_card, _fetch_pinned_pricing_guide

    g, lab = await _fetch_pinned_pricing_guide(), await _fetch_pinned_labor_rate_card()
    (out / "guide.md").write_text(g[0] if g else "")
    (out / "labor.md").write_text(lab[0] if lab else "")


async def main(argv: list[str]) -> None:
    if argv[0] == "--fetch-kb":
        return await fetch_kb(Path(argv[1]))
    global RUN_TAG
    if argv[0].startswith("--tag="):
        RUN_TAG, argv = "." + argv[0][6:], argv[1:]
    out = Path(argv[0])
    guide_md, labor_md = (out / "guide.md").read_text(), (out / "labor.md").read_text()
    guide, labor = parse_guide(guide_md), parse_labor(labor_md)
    missing = [k for k, v in guide["items"].items() if k != "6.1" and len(v["tiers"]) != 3]
    assert len(guide["items"]) >= 40 and len(guide["verbatim"]) == 5 and len(labor) >= 8 and not missing, (
        f"KB parse failed: missing bands {missing}"
    )
    reports = await asyncio.gather(*(run_one(Path(p), out, guide_md, guide, labor_md, labor) for p in argv[1:]),
                                   return_exceptions=True)
    for r in reports:
        print(json.dumps(r if isinstance(r, dict) else {"error": repr(r)}, indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
