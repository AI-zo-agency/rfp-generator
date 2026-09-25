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
import sys
from pathlib import Path

from app.services import llm
from app.services.pricing_plan_engine import (  # noqa: F401 — used below
    BANNED, BILLING, BLOCK_TOKENS, TOKEN_RE, VERBATIM_KEYS,
    compute, decide_tier, parse_guide, parse_labor, render, verify_asks, verify_plan,
)

MODEL_KW = dict(tier="heavy", node_name="phase-3-5-budget-proto", include_corrections=False)
RUN_TAG = ""


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
