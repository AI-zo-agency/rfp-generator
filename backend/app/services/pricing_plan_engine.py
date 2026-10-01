"""Pricing plan engine (zö pricing methodology v2): pure code, no I/O.

The LLM describes the work (catalog items, or hours by role plus POs for custom
work); this module is the only place money is computed. Prices come from rules:
a catalog item sells at its Pricing Book price, custom work at a multiple of its
all-in cost between the floor (cost / 0.47) and the target (3.7x), chosen to fit
the RFP budget. Every figure comes from the PricingBook (pricing_kb), none from
this file. Every check here is what the repair loop feeds back to the LLM.
"""

from __future__ import annotations

import math
import re
from datetime import date

from app.services.pricing_kb import PO_COLUMNS, PricingBook

ENGAGEMENT_TYPES = (
    "fixed_quote", "monthly_retainer", "time_materials", "time_deliverables", "not_to_exceed", "procurement",
)
CLIENT_KINDS = ("government", "nonprofit", "private")
BILLING = ("one_time", "monthly", "per_event")
MEDIA_KINDS = ("traditional", "digital")
PO_TOPICS = (*PO_COLUMNS, "Other POs")
BLOCK_TOKENS = {"TASK_TABLE", "RATE_TABLE", "FORM", "MEDIA_SPLIT", "VERBATIM"}
VERBATIM_KEYS = ("billing", "outside", "changes", "travel", "rates")
REQUIRED_VERBATIM = ("billing", "outside", "changes")
MGMT_ROLES = ("PM", "AM", "LD")  # project, account management and Agency Director oversight
TM_BLOCK = "Time & Materials, Time & Deliverables, Not to Exceed"
ENGAGEMENT_BLOCK = {
    "fixed_quote": "Fixed Quote, private client", "monthly_retainer": "Monthly Retainer",
    "time_materials": TM_BLOCK, "time_deliverables": TM_BLOCK, "not_to_exceed": TM_BLOCK,
    "procurement": "Production",
}
ENGAGEMENT_TERMS = {
    "fixed_quote": "Standard 50/25/25", "monthly_retainer": "Retainer Monthly",
    "time_materials": "Lump Sum at Completion", "time_deliverables": "Lump Sum at Completion",
    "not_to_exceed": "Lump Sum at Completion", "procurement": "Production 100",
}
WORDING_KEY = {"outside": "Outside the price", "changes": "Change orders", "travel": "Travel", "rates": "Rates"}
# Client prose never carries internal numbers, vendors, people or hours talk.
BANNED = re.compile(
    r"(?i)margin|\bloaded\b|markup|all-in|gross profit|floor price|\bPOs?\b|hard cost|"
    r"\bhourly\b|per hour|an hour|hours? (?:per|a|each|by)|\bFTE\b|staffing (?:mix|hours)|"
    r"sonja|\bella\b|curt schultz|justin bronson|citizen & co|gil aranowitz|ben edwards|\btreeline\b|"
    r"morgan nivan|tisha hopper|\be2m\b|"
    r"internal rate|raw floor|labor cost|00_guide|pricing (?:book|internal)"
)
TOKEN_RE = re.compile(r"\{\{([A-Z_]+)(?::([^}]+))?\}\}")
SLOT_RE = re.compile(r"\[([^\[\]]+)\]")
TOTAL_KEY = "__total__"


# ---------------------------------------------------------------- small helpers


def money(s: str) -> float:
    return float(s.replace(",", "").replace("$", ""))


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9$]+", " ", (s or "").lower()).strip()


def months(plan: dict) -> int:
    m = plan.get("term_months")
    return int(m) if isinstance(m, (int, float)) and 1 <= m <= 120 else 12


def blended_rate(plan: dict, book: PricingBook) -> float:
    """$275 for every role, or the negotiated rate when the client has one (City of Bend)."""
    name = _norm(plan.get("client_name") or "")
    for client, rate in book.negotiated_rates.items():
        if name and _norm(client) in name:
            return rate
    return book.settings.blended_rate


def usd(v, cents: bool = False) -> str:
    if v is None:
        return "[MANUAL FILL]"
    return f"${v:,.2f}" if cents and v != int(v) else f"${v:,.0f}"


def media_fee(book: PricingBook, monthly_spend: float) -> float:
    """Digital media management fee for one month of ad spend (taper by spend, with the minimum fee)."""
    tier = next((t for t in book.media_fees if monthly_spend <= t.up_to), book.media_fees[-1])
    return max(monthly_spend * tier.percent / 100, max(t.minimum for t in book.media_fees))


def _round_price(price: float, floor: float, billing: str) -> float:
    unit = 50 if billing in ("monthly", "per_event") else 100
    return max(round(price / unit) * unit, math.ceil(floor / unit) * unit)


# ---------------------------------------------------------------- compute


def _build(t: dict, book: PricingBook) -> dict | None:
    """One task's cost per billing period (quantity included) and, when rules fix it, its price."""
    qty = _f(t.get("quantity")) or 1.0
    code = t.get("catalog_code")
    if code:
        item = book.catalog.get(code)
        if item is None:
            return None
        return {"kind": "catalog", "cost": book.all_in_cost(item) * qty, "hours": sum(item.hours.values()) * qty,
                "fixed": item.price * qty}
    b = t.get("build")
    if not isinstance(b, dict):
        return None
    hours = {k: _f(v) for k, v in (b.get("hours") or {}).items()}
    cost = (
        sum(book.roles[k].loaded * h for k, h in hours.items() if k in book.roles)
        + sum(_f(v) for v in (b.get("pos") or {}).values())
        + _f(b.get("hard_cost"))
    ) * qty
    row = {"kind": "custom", "cost": cost, "hours": sum(hours.values()) * qty, "fixed": None}
    spend = _f(t.get("media_spend"))
    if t.get("media_kind") == "traditional" and spend:
        row.update(kind="media_traditional", fixed=spend, rev_rate=book.settings.traditional_commission)
    elif t.get("media_kind") == "digital" and spend:
        row.update(kind="media_digital", fixed=media_fee(book, spend))
    return row


def compute(plan: dict, book: PricingBook) -> dict:
    """Derive every money figure from the plan. The LLM's numbers are inputs, never outputs."""
    n = months(plan)
    tasks = {t["task_id"]: t for t in plan.get("tasks", []) if t.get("task_id")}
    rows = {tid: r for tid, t in tasks.items() if (r := _build(t, book))}
    span = {tid: (n if t.get("billing") == "monthly" else 1) for tid, t in tasks.items()}
    summed = {tid for tid, t in tasks.items() if t.get("billing", "one_time") != "per_event"}
    oh_hours = {k: _f(v) for k, v in ((plan.get("overhead") or {}).get("hours") or {}).items()}
    oh_cost = sum(book.roles[k].loaded * h for k, h in oh_hours.items() if k in book.roles)

    custom = [tid for tid, r in rows.items() if r["kind"] == "custom" and tid in summed]
    custom_term = sum(rows[tid]["cost"] * span[tid] for tid in custom)
    multiples = (plan.get("pricing") or {}).get("multiples") or {}
    fallback = min(book.settings.target_multiple, max(1 / book.settings.cost_ratio, 1 / 0.40))

    amounts, revenue, cost_term, hours_term = {}, {}, {}, {}
    for tid, r in rows.items():
        t, sp = tasks[tid], span[tid]
        # management hours are recovered in the prices of custom work, in proportion to each task's cost
        alloc = oh_cost * r["cost"] / custom_term if tid in custom and custom_term else 0.0
        cost = r["cost"] + alloc
        if r["fixed"] is not None:
            amount = r["fixed"]
        else:
            m = multiples.get(t.get("track") if t.get("track") in multiples else TOTAL_KEY, fallback)
            floor = cost / book.settings.cost_ratio
            amount = _round_price(cost * m, floor, t.get("billing", "one_time"))
        amounts[tid] = amount
        if tid in summed:
            revenue[tid] = amount * r.get("rev_rate", 1.0) * sp
            cost_term[tid], hours_term[tid] = cost * sp, r["hours"] * sp
    unallocated = 0.0 if custom_term else oh_cost

    def total(track=None, billing="one_time", media=None) -> float:
        if track is not None and not any(t.get("track") == track for t in tasks.values()):
            track = None  # single-ceiling label, not a track
        return sum(
            a for tid, a in amounts.items()
            if tasks[tid].get("billing", "one_time") == billing
            and (track is None or tasks[tid].get("track") == track)
            and (media is None or media == rows[tid]["kind"].startswith("media_traditional"))
        )

    rate = blended_rate(plan, book)
    fills = {}
    for f in plan.get("form_fills", []):
        ids, k = f.get("task_ids") or [], f.get("kind")
        val = {"task_price": sum(amounts.get(i, 0) for i in ids) if ids else None,
               "labor_rate": rate, "rate": rate}.get(k)  # hours and extended stay blank: hours are internal
        fills[(f.get("row_id"), f.get("column"))] = (k, val)
    return {
        "amounts": amounts, "revenue": revenue, "cost_term": cost_term, "hours_term": hours_term,
        "rows": rows, "tasks": tasks, "total": total, "fills": fills, "months": n, "rate": rate,
        "overhead": {"cost": oh_cost, "hours": oh_hours, "unallocated": unallocated},
        "custom_cost_term": custom_term, "multiples": multiples,
    }


def term_value(c: dict, track=None, media=None) -> float:
    """One-time work plus the term's monthly fees. per_event fees are rates, never summed."""
    return c["total"](track, "one_time", media) + c["months"] * c["total"](track, "monthly", media)


def margins(c: dict) -> dict:
    """Revenue, cost and hours over the term by group and in total (unallocated overhead is in the total)."""
    groups: dict[str, dict] = {}
    for tid in c["cost_term"]:
        g = groups.setdefault(c["tasks"][tid].get("group") or "Other", {"revenue": 0.0, "cost": 0.0, "hours": 0.0})
        g["revenue"] += c["revenue"][tid]
        g["cost"] += c["cost_term"][tid]
        g["hours"] += c["hours_term"][tid]
    tot = {"revenue": sum(g["revenue"] for g in groups.values()),
           "cost": sum(g["cost"] for g in groups.values()) + c["overhead"]["unallocated"],
           "hours": sum(g["hours"] for g in groups.values()) + sum(c["overhead"]["hours"].values())}
    for g in (*groups.values(), tot):
        g["margin"] = 1 - g["cost"] / g["revenue"] if g["revenue"] else None
        g["per_hour"] = g["revenue"] / g["hours"] if g["hours"] else None
    return {"groups": groups, "total": tot}


# ---------------------------------------------------------------- pricing (budget fit)


def budgets_from_asks(asks: dict, target_budget_usd: float | None = None) -> dict[str, float]:
    """The budget our price must fit, per track. Shared pools are not ours; one ceiling covers the total."""
    own = [x for x in asks.get("ceilings", []) if not x.get("shared_pool") and _f(x.get("amount"))]
    out = {}
    for x in own:
        key = x.get("track") if len(own) > 1 and x.get("track") else (x.get("label") if len(own) > 1 else TOTAL_KEY)
        out[key or TOTAL_KEY] = out.get(key or TOTAL_KEY, 0.0) + _f(x["amount"])
    if not out and target_budget_usd:
        out[TOTAL_KEY] = float(target_budget_usd)
    return out


def price_plan(plan: dict, book: PricingBook, budgets: dict[str, float]) -> dict:
    """Choose the cost multiple for custom work per budget scope (methodology section 9) and record it in the plan.

    Start at the target multiple. Over the budget: come down to about 90% of it, never below the floor.
    No budget: start near cost / 0.40. Returns {"no_fit": {scope: {...}}} when even the floor is over budget.
    """
    s = book.settings
    floor_m, target_m = 1 / s.cost_ratio, s.target_multiple
    plan.pop("pricing", None)
    c = compute(plan, book)  # custom tasks fall back to a multiple here; only fixed prices and costs are used below
    scopes: dict[str, dict] = {}
    for tid, t in c["tasks"].items():
        if tid not in c["cost_term"]:
            continue
        key = t.get("track") if t.get("track") in budgets else (TOTAL_KEY if TOTAL_KEY in budgets else None)
        sc = scopes.setdefault(key, {"fixed": 0.0, "custom": 0.0})
        if c["rows"][tid]["fixed"] is not None:
            sc["fixed"] += c["amounts"][tid] * (c["months"] if t.get("billing") == "monthly" else 1)
        else:
            sc["custom"] += c["cost_term"][tid]
    multiples, no_fit = {}, {}
    for key, sc in scopes.items():
        budget = budgets.get(key) if key else None
        if budget is None:
            m = min(target_m, max(floor_m, 1 / 0.40))
        elif sc["fixed"] + sc["custom"] * target_m <= budget:
            m = target_m
        else:
            goal = 0.9 * budget - sc["fixed"]
            m = max(goal / sc["custom"], floor_m) if sc["custom"] else floor_m
            floor_total = sc["fixed"] + sc["custom"] * floor_m
            if floor_total > budget:
                no_fit[key] = {"floor_total": floor_total, "budget": budget}
        multiples[key or TOTAL_KEY] = round(min(m, target_m), 4)
    plan["pricing"] = {"multiples": multiples, "budgets": budgets}
    return {"no_fit": no_fit}


def cut_suggestions(plan: dict, book: PricingBook, over_by: float, track: str | None = None) -> list[str]:
    """Largest priced items first, until their term price covers `over_by` (for the go/no-go and internal notes)."""
    c = compute(plan, book)
    scope = [
        (c["amounts"][tid] * (c["months"] if t.get("billing") == "monthly" else 1), tid, t)
        for tid, t in c["tasks"].items()
        if tid in c["cost_term"] and (track in (None, TOTAL_KEY) or t.get("track") == track)
    ]
    out, saved = [], 0.0
    for price, tid, t in sorted(scope, key=lambda x: x[0], reverse=True):
        if saved >= over_by:
            break
        out.append(f"{tid} {t.get('deliverable')} saves {usd(price)}")
        saved += price
    return out


# ---------------------------------------------------------------- wording


def fills_for(plan: dict) -> dict[str, str]:
    base = {"client": plan.get("client_name") or "",
            "retainer or quote": "retainer" if plan.get("engagement_type") == "monthly_retainer" else "quote"}
    base.update({_norm(k): str(v) for k, v in (plan.get("fills") or {}).items()})
    return {_norm(k): v for k, v in base.items() if v}


def wording_text(plan: dict, book: PricingBook, key: str) -> str:
    """An approved block for the plan's engagement and client, slots filled from the plan."""
    eng = plan.get("engagement_type")
    if key == "billing":
        text = book.wording.get(ENGAGEMENT_BLOCK.get(eng, ""), "")
        if plan.get("client_kind") == "government":
            gov = book.wording.get("Government contract", "")
            # government bills monthly, so the deposit and milestone lines of the engagement block go
            keep = [l for l in text.splitlines() if not re.search(r"50%|deposit|15th of the month|100%|on signing", l, re.I)]
            text = gov + "\n" + "\n".join(keep)
    else:
        text = book.wording.get(WORDING_KEY.get(key, ""), "")
    fills = fills_for(plan)
    return SLOT_RE.sub(lambda m: fills.get(_norm(m.group(1)), m.group(0)), text).strip()


def billing_terms_name(plan: dict) -> str | None:
    if plan.get("client_kind") == "government":
        return "Government Monthly"
    return ENGAGEMENT_TERMS.get(plan.get("engagement_type"))


# ---------------------------------------------------------------- verify


def verify_asks(asks: dict, rfp: str) -> list[str]:
    body = _norm(rfp)
    errs = []
    quoted = [("ask " + a.get("id", "?"), a.get("quote")) for a in asks.get("asks", [])]
    quoted += [("ceiling " + c.get("label", "?"), c.get("quote")) for c in asks.get("ceilings", [])]
    client = asks.get("client") or {}
    if client.get("kind") and client.get("quote"):
        quoted.append(("client", client.get("quote")))
    if not asks.get("priced_scope"):
        errs.append("priced_scope is empty — list every SOW deliverable")
    if client.get("kind") not in CLIENT_KINDS:
        errs.append(f"client.kind must be one of {list(CLIENT_KINDS)}")
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


def _task_errors(plan: dict, book: PricingBook, rfp_text: str | None) -> list[str]:
    errs, seen = [], set()
    for t in plan.get("tasks", []):
        tid = t.get("task_id")
        if tid in seen:
            errs.append(f"{tid}: duplicate task_id")
        seen.add(tid)
        if t.get("billing", "one_time") not in BILLING:
            errs.append(f"{tid}: billing {t.get('billing')!r} invalid — zö never bills hourly; use {list(BILLING)}")
        if t.get("billing") == "monthly" and _f(t.get("quantity")) not in (0.0, 1.0):
            errs.append(f"{tid}: a monthly fee has quantity 1 (the term sets the months)")
        code, b, kind = t.get("catalog_code"), t.get("build"), t.get("media_kind")
        if code:
            item = book.catalog.get(code)
            if item is None:
                errs.append(f"{tid}: catalog code {code!r} is not in the Pricing Book")
            elif _norm(t.get("catalog_item") or "") != _norm(item.item):
                errs.append(f"{tid}: catalog code {code} is {item.item!r}, not {t.get('catalog_item')!r} — fix the code or the name")
            if b:
                errs.append(f"{tid}: a catalog item has no build; code copies its hours and costs")
        elif isinstance(b, dict):
            bad = [k for k in (b.get("hours") or {}) if k not in book.roles]
            if bad:
                errs.append(f"{tid}: build roles {bad} are not roles in Pricing Internal ({list(book.roles)})")
            bad = [k for k in (b.get("pos") or {}) if k not in PO_TOPICS]
            if bad:
                errs.append(f"{tid}: build PO types {bad} must be one of {list(PO_TOPICS)}")
            if not str(b.get("basis") or "").strip():
                errs.append(f"{tid}: build needs a basis (the task library entry or the reasoning for the estimate)")
            if not any(_f(v) for v in (b.get("hours") or {}).values()) and not any(_f(v) for v in (b.get("pos") or {}).values()):
                errs.append(f"{tid}: build has no hours and no POs")
        elif not t.get("manual_reason"):
            errs.append(f"{tid}: needs catalog_code, build, or manual_reason")
        if kind and kind not in MEDIA_KINDS:
            errs.append(f"{tid}: media_kind must be one of {list(MEDIA_KINDS)}")
        if kind and not _f(t.get("media_spend")):
            errs.append(f"{tid}: media task needs media_spend (the client's media budget; monthly for digital)")
        if kind == "digital" and t.get("billing") != "monthly":
            errs.append(f"{tid}: digital media management is a monthly fee")
        if _f(t.get("quantity")) not in (0.0, 1.0):
            basis = t.get("quantity_basis")
            if basis not in ("rfp", "assumption"):
                errs.append(f"{tid}: quantity {t.get('quantity')} needs quantity_basis 'rfp' (with quantity_quote) or 'assumption'")
            elif basis == "rfp" and rfp_text is not None and _norm(t.get("quantity_quote") or "")[:120] not in _norm(rfp_text):
                errs.append(f"{tid}: quantity_quote not found verbatim in RFP")
    return errs


def verify_plan(
    plan: dict, asks: dict, book: PricingBook, *, budgets: dict[str, float] | None = None,
    rfp_text: str | None = None, today: date | None = None, report: dict | None = None,
) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    s = book.settings
    if plan.get("engagement_type") not in ENGAGEMENT_TYPES:
        errs.append(f"engagement_type must be one of {list(ENGAGEMENT_TYPES)}")
    if plan.get("client_kind") not in CLIENT_KINDS:
        errs.append(f"client_kind must be one of {list(CLIENT_KINDS)}")
    if not isinstance(plan.get("term_months"), (int, float)) or not 1 <= plan["term_months"] <= 120:
        errs.append("term_months must be the contract term in months (1-120)")
    covered = {x for t in plan.get("tasks", []) for x in t.get("scope_ids") or []}
    missing_scope = [x["id"] for x in asks.get("priced_scope", []) if x.get("id") not in covered]
    if missing_scope:
        errs.append(f"SOW items with no task: {missing_scope}")
    tracks = {x.get("track") for x in asks.get("ceilings", []) if x.get("track")}
    labels = tracks | {x.get("label") for x in asks.get("ceilings", [])}
    for t in plan.get("tasks", []):
        if t.get("track") and tracks and t["track"] not in tracks:
            errs.append(f"{t.get('task_id')}: track {t['track']!r} is not a ceiling label {sorted(tracks)}")
    errs += _task_errors(plan, book, rfp_text)
    if errs:  # money checks need a sound plan
        return errs, warns

    c = compute(plan, book)
    m = margins(c)
    today = today or date.today()
    priced = [tid for tid in c["amounts"] if tid in c["cost_term"]]
    if len(priced) > 1 or any(c["rows"][tid]["kind"] == "custom" for tid in priced):
        oh = c["overhead"]["hours"]
        gone = [r for r in MGMT_ROLES if not oh.get(r)]
        if gone:
            errs.append(f"overhead.hours must include project management, account management and Agency Director "
                        f"oversight (roles {list(MGMT_ROLES)}); missing {gone}")
    # catalog items below the floor: allowed alone through the price hold date, always flagged
    expired = book.is_expired(today)
    for code in dict.fromkeys(c["tasks"][tid]["catalog_code"] for tid in priced if c["rows"][tid]["kind"] == "catalog"):
        it = book.catalog[code]
        if it.price < book.all_in_cost(it) / s.cost_ratio:
            msg = f"catalog {code} {it.item} sells below the {s.margin_floor:.0%} floor at its Pricing Book price"
            if expired:
                errs.append(f"{msg}, and the price hold has ended: Sonja decides")
            else:
                warns.append(f"{msg} — flag for Sonja")
    if expired:
        warns.append(f"Pricing {book.version} price hold ended {book.valid_through:%B %d, %Y}; ask for the new price list")
    catalog_only = lambda tids: all(c["rows"][i]["kind"] == "catalog" for i in tids)  # noqa: E731
    by_group = {g: [tid for tid in priced if (c["tasks"][tid].get("group") or "Other") == g] for g in m["groups"]}
    for name, g in (*m["groups"].items(), ("the total", m["total"])):
        tids = priced if name == "the total" else by_group[name]
        out = errs if not catalog_only(tids) else warns
        if g["margin"] is not None and g["margin"] < s.margin_floor - 1e-9:
            out.append(f"{name} holds {g['margin']:.1%} gross profit, below the {s.margin_floor:.0%} floor — "
                       "fix the build or the scope, or raise it for Sonja")
        if g["per_hour"] is not None and g["per_hour"] < s.min_price_per_hour:
            out.append(f"{name} prices at ${g['per_hour']:,.0f} per in-house hour, under ${s.min_price_per_hour:,.0f} — "
                       "hours are probably missing or the price is short")
    # budget
    for key, info in (report or {}).get("no_fit", {}).items():
        errs.append(f"scope does not fit the budget ({'total' if key in (None, TOTAL_KEY) else key}): the floor price is "
                    f"{info['floor_total']:,.0f}, the budget {info['budget']:,.0f}. Cut or phase scope — see internal notes")
    for key, budget in (budgets or {}).items():
        spent = term_value(c, None if key == TOTAL_KEY else key)
        if spent > budget + 0.5 and key not in (report or {}).get("no_fit", {}):
            errs.append(f"priced {spent:,.0f} exceeds the budget {budget:,.0f}")
    if not budgets:
        warns.append("unanchored: no RFP budget and no target budget — total needs human review")
    notes = " ".join(n.get("issue", "") for n in plan.get("internal_notes", [])).lower()
    for lim in asks.get("implied_limits") or []:
        warns.append(f"implied limit: {lim.get('label')} — {lim.get('note')}")
        if not any(w in notes for w in _norm(lim.get("label", "")).split() if len(w) > 5):
            errs.append(f"implied limit {lim.get('label')!r} not raised in internal_notes")
    for tid, t in c["tasks"].items():
        if t.get("quantity_basis") == "assumption":
            warns.append(f"assumption: {tid} quantity {t.get('quantity')} is not stated in the RFP")
    # form coverage
    form = asks.get("buyer_form")
    if form:
        for row in form.get("rows", []):
            for col in form.get("columns", []):
                if (row["row_id"], col) not in c["fills"]:
                    errs.append(f"form cell {row['row_id']}/{col} ({row['label']}) has no fill")
        for (rid, col), (k, v) in c["fills"].items():
            if k in {"hours", "extended"}:
                warns.append(f"form cell {rid}/{col}: the RFP asks for hours; zö publishes prices only — left for Sonja")
            elif k not in {"manual", "hours", "extended"} and v is None:
                errs.append(f"form fill {rid}/{col} kind={k} computed nothing (no tasks listed)")
    # prose
    body = "\n".join(x.get("body_md", "") for x in plan.get("sections", []))
    found = [mm.groups() for mm in TOKEN_RE.finditer(body)]
    for name, arg in found:
        if name == "VERBATIM" and arg not in VERBATIM_KEYS:
            errs.append(f"unknown verbatim block {arg}; use {list(VERBATIM_KEYS)}")
        elif name == "VERBATIM" and not wording_text(plan, book, arg):
            errs.append(f"verbatim block {arg} has no approved wording")
        if name == "AMT" and arg not in c["amounts"]:
            errs.append(f"{{{{AMT:{arg}}}}} references an unpriced/unknown task")
        if name in {"TOTAL", "TASK_TABLE"} and arg and arg not in labels:
            errs.append(f"{{{{{name}:{arg}}}}} track not a ceiling label")
        if name not in BLOCK_TOKENS | {"TOTAL", "AMT", "RATE"}:
            errs.append(f"unknown token {{{{{name}}}}} — hours and staffing never appear; prices only")
    blocks = [(n, a) for n, a in found if n in BLOCK_TOKENS]
    for dup in {b for b in blocks if blocks.count(b) > 1}:
        errs.append(f"block token {dup[0]}{':' + dup[1] if dup[1] else ''} used more than once")
    rendered_text = [("prose", TOKEN_RE.sub("", body))]
    rendered_text += [(f"section heading {x.get('heading')!r}", x.get("heading") or "") for x in plan.get("sections", [])]
    for t in plan.get("tasks", []):
        rendered_text += [(f"{t.get('task_id')} {k}", t.get(k) or "") for k in ("deliverable", "group")]
    for label, text in rendered_text:
        if re.search(r"\$\s?\d", text):
            errs.append(f"{label} contains a literal dollar figure" + (" — use tokens" if label == "prose" else ""))
        hit = BANNED.search(text)
        if hit:
            errs.append(f"{label} contains internal jargon: {hit.group(0)!r}")
    all_in = bool((asks.get("all_inclusive_pricing") or {}).get("value"))
    for k in REQUIRED_VERBATIM:
        if f"{{{{VERBATIM:{k}}}}}" not in body:
            errs.append(f"missing {{{{VERBATIM:{k}}}}}")
    if any(c["rows"][i]["kind"] == "media_traditional" for i in c["rows"]) and "MEDIA_SPLIT" not in {n for n, _ in found}:
        errs.append("traditional media used but {{MEDIA_SPLIT}} missing")
    for key in REQUIRED_VERBATIM:
        unfilled = SLOT_RE.findall(wording_text(plan, book, key))
        if unfilled:
            errs.append(f"{{{{VERBATIM:{key}}}}} has unfilled slots {unfilled} — add them to plan.fills")
    if all_in:
        warns.append("RFP requires all-inclusive pricing: outside-the-price wording must not add separate expenses")
    used = {n for n, _ in found}
    need = {"cost_per_task": ("TASK_TABLE",), "cost_per_deliverable": ("TASK_TABLE",), "itemized_budget": ("TASK_TABLE",),
            "hourly_rates": ("RATE_TABLE", "FORM")}
    for a in asks.get("asks", []):
        want = need.get(a.get("kind"))
        if a.get("required") and want and not used & set(want):
            errs.append(f"required ask {a['id']} ({a['kind']}) not answered: needs {' or '.join(want)}")
        if a.get("required") and a.get("kind") in {"staffing_matrix", "hours_per_task"}:
            warns.append(f"ask {a['id']} wants hours or staffing; zö publishes prices only — raised for Sonja")
    if form and "FORM" not in used:
        errs.append("buyer form present but {{FORM}} not placed")
    outline = (asks.get("compensation_outline") or {}).get("items") or []
    if outline and len(plan.get("sections", [])) < len(outline):
        warns.append(f"RFP compensation outline has {len(outline)} items; plan has {len(plan.get('sections', []))} sections")
    return errs, warns


# ---------------------------------------------------------------- render (client-safe)


SUFFIX = {"monthly": " / month", "per_event": " / event", "one_time": ""}


def render(plan: dict, asks: dict, book: PricingBook) -> str:
    c = compute(plan, book)
    tasks = plan.get("tasks", [])
    rate = c["rate"]

    def task_table(track=None) -> str:
        rows = ["| Task ID | Task / Deliverable | Investment |", "|---|---|---:|"]
        scoped = [t for t in tasks if track is None or t.get("track") == track]
        for group in dict.fromkeys(t.get("group") for t in scoped):  # first-appearance order
            rows.append(f"| | **{group}** | |")
            for t in (t for t in scoped if t.get("group") == group):
                amt = c["amounts"].get(t["task_id"])
                rows.append(f"| {t['task_id']} | {t['deliverable']} | {usd(amt)}{SUFFIX[t.get('billing', 'one_time')] if amt else ''} |")
        billings = {t.get("billing", "one_time") for t in scoped}
        if billings & {"one_time", "monthly"}:
            label = "Total (term value)" if "monthly" in billings else "Total"
            rows.append(f"| | **{label}** | **{usd(term_value(c, track))}** |")
        return "\n".join(rows)

    def rate_table(_=None) -> str:
        roles = plan.get("hourly_roles") or ["All roles"]
        return "\n".join(["| Role | Hourly Rate |", "|---|---:|"] + [f"| {r} | {usd(rate)} |" for r in roles])

    def form(_=None) -> str:
        f = asks.get("buyer_form") or {}
        cols = f.get("columns") or ["Value"]
        rows = [f"**{f.get('name', 'Pricing Form')}**", "", "| Item | UOM | " + " | ".join(cols) + " |", "|---|---|" + "---:|" * len(cols)]
        for r in f.get("rows", []):
            cells = []
            for col in cols:
                k, v = c["fills"].get((r["row_id"], col), (None, None))
                cells.append("" if k is None else usd(v, cents=True))
            rows.append(f"| {r['label']} | {r.get('unit', '')} | " + " | ".join(cells) + " |")
        return "\n".join(rows)

    def media_split(_=None) -> str:
        cm = book.settings.traditional_commission
        rows = [f"| Media | Total | Placements ({1 - cm:.0%}) | Agency ({cm:.0%}) |", "|---|---:|---:|---:|"]
        for t in tasks:
            if t.get("media_kind") == "traditional":
                amt = c["amounts"].get(t["task_id"], 0)
                rows.append(f"| {t['task_id']} {t['deliverable']} | {usd(amt)} | {usd(amt * (1 - cm))} | {usd(amt * cm)} |")
        return "\n".join(rows)

    table = {
        "TASK_TABLE": task_table, "RATE_TABLE": rate_table, "FORM": form, "MEDIA_SPLIT": media_split,
        "TOTAL": lambda a=None: usd(term_value(c, a)),
        "AMT": lambda a: usd(c["amounts"].get(a)),
        "RATE": lambda a=None: usd(rate),
        "VERBATIM": lambda a: wording_text(plan, book, a),
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


def internal_summary(plan: dict, book: PricingBook) -> str:
    """Internal-only block: price, cost, margin and hours behind the budget. Never part of the client Cost section."""
    c = compute(plan, book)
    m = margins(c)
    s = book.settings
    rows = ["| Group | Price | All-in cost | Gross profit | In-house hours | Price per hour |", "|---|---:|---:|---:|---:|---:|"]
    for name, g in (*m["groups"].items(), ("**Total**", m["total"])):
        margin = f"{g['margin']:.1%}" if g["margin"] is not None else "n/a"
        per_hour = usd(g["per_hour"]) if g["per_hour"] is not None else "n/a"
        rows.append(f"| {name} | {usd(g['revenue'])} | {usd(g['cost'])} | {margin} | {g['hours']:,.0f} | {per_hour} |")
    total = m["total"]["revenue"]
    out = ["**INTERNAL — DO NOT PLACE**", "",
           f"Pricing {book.version} · {plan.get('engagement_type')} · {plan.get('client_kind')} · term {c['months']} months", "",
           *rows, "",
           f"Floor price {usd(m['total']['cost'] / s.cost_ratio)} · target {usd(m['total']['cost'] * s.target_multiple)} · "
           f"nonprofit option ({s.nonprofit_discount:.0%} off): {usd(total * (1 - s.nonprofit_discount))}", ""]
    out += ["| # | Issue | Owner |", "|---|---|---|"]
    out += [f"| {i} | {n.get('issue', '')} | {n.get('owner', '')} |" for i, n in enumerate(plan.get("internal_notes", []), 1)]
    return "\n".join(out) + "\n"
