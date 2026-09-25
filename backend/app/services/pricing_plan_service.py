"""Pricing plan v2: RFP asks -> LLM plan -> code checks/repair -> ProposalBudget.

All money math and checks live in pricing_plan_engine; this module only talks
to the LLM and the KB and adapts the plan onto ProposalBudget.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import NamedTuple

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services import llm
from app.services.pricing_plan_engine import (
    compute,
    decide_tier,
    parse_guide,
    parse_labor,
    render,
    usd,
    verify_asks,
    verify_plan,
)
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

NODE = "pricing-plan-v2"
MAX_REPAIRS = 2
_UNIT = {"one_time": "project", "monthly": "month", "per_event": "event", "hourly": "hour"}

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
- Term value = one_time tasks + 12 x monthly fees (per_event and hourly are rates and are not summed).
- A ceiling that is ours alone: term value EXCLUDING Guide 6.1 media must total 65-85% of (ceiling minus media)
  (leave 15-20% for expansion, but do not leave the buyer's budget mostly unused). Media is excluded from
  the 65-85% rule and must never be used to reach it. Adjust quantities and scope depth of fee work to land there.
  shared_pool ceilings are not ours: size the bid to the scope and do not claim the pool as our NTE.
- Term value per track, media included, must stay <= that track's ceiling.
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


def guide_menu(guide: dict) -> str:
    return "\n".join(
        f"{gid} {it['name']}: " + "; ".join(f"{t} {lo:,.0f}-{hi:,.0f}" for t, (lo, hi) in it["tiers"].items())
        for gid, it in guide["items"].items()
    )


EDIT_PROMPT = """You update an existing zö agency pricing plan to follow the user's instruction.
Return the FULL updated plan JSON (same schema as the plan you are given) plus a top-level
"reply": one or two sentences telling the user what changed.
- Change only what the instruction asks. Keep task_ids stable for unchanged tasks.
- Every pricing and writing rule below still applies; the tier is fixed.
- If the instruction asks for something the rules forbid (price outside the band, a rate not on the
  Labor Cost card, a dollar figure in prose), do not do it — explain why in "reply".

""" + AUTHOR_PROMPT


class PricingKb(NamedTuple):
    guide_md: str
    guide: dict
    labor_md: str
    labor: dict


async def _call(system: str, user: str, max_tokens: int = 24000) -> dict:
    raw, _provider = await llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.2,
        tier="heavy",
        node_name=NODE,
        reasoning_effort="medium",
    )
    return raw


async def load_pricing_kb() -> PricingKb:
    from app.services.proposal_pricing_service import (
        _fetch_pinned_labor_rate_card,
        _fetch_pinned_pricing_guide,
    )

    guide_hit = await _fetch_pinned_pricing_guide()
    labor_hit = await _fetch_pinned_labor_rate_card()
    if not guide_hit or not labor_hit:
        raise ProposalError("Pricing Guide or Labor Cost card not found in the KB.", status_code=424)
    guide, labor = parse_guide(guide_hit[0]), parse_labor(labor_hit[0])
    missing = [k for k, v in guide["items"].items() if k != "6.1" and len(v["tiers"]) != 3]
    if len(guide["verbatim"]) < 4 or not labor or missing:
        raise ProposalError(
            f"Pricing Guide / Labor Cost card did not parse (bands missing: {missing}).", status_code=424
        )
    return PricingKb(guide_hit[0], guide, labor_hit[0], labor)


async def extract_pricing_asks(rfp_text: str) -> tuple[dict, list[str]]:
    asks = await _call(ASKS_PROMPT, f"=== RFP ===\n{rfp_text}", 10000)
    errs = verify_asks(asks, rfp_text)
    if errs:
        asks = await _call(
            ASKS_PROMPT,
            f"=== RFP ===\n{rfp_text}\n\n=== YOUR PREVIOUS JSON ===\n{json.dumps(asks)}\n\n"
            "=== FIX THESE (quote exactly or drop the item) ===\n" + "\n".join(errs),
            10000,
        )
        errs = verify_asks(asks, rfp_text)
    return asks, errs


def _context(rfp_text: str, asks: dict, kb: PricingKb, tier: str, why: str, target: float | None) -> str:
    target_line = (
        f"=== TARGET BUDGET (set by the agency): {target:,.0f} — priced work must total 90-110% of it ===\n\n"
        if target
        else ""
    )
    return (
        f"=== TIER (decided by code): {tier} — {why} ===\n\n{target_line}"
        f"=== PRICING ASKS (extracted, verified) ===\n{json.dumps(asks, indent=1)}\n\n"
        f"=== PRICING GUIDE MENU (parsed bands) ===\n{guide_menu(kb.guide)}\n\n"
        f"=== PRICING GUIDE (full) ===\n{kb.guide_md}\n\n=== LABOR COST CARD ===\n{kb.labor_md}\n\n"
        f"=== RFP ===\n{rfp_text}"
    )


async def _check_and_repair(
    plan: dict, asks: dict, kb: PricingKb, *, system: str, ctx: str, target: float | None
) -> tuple[dict, list[dict]]:
    if not isinstance(plan, dict):
        raise ProposalError("Pricing plan LLM returned no plan", status_code=502)
    rounds: list[dict] = []
    for attempt in range(MAX_REPAIRS + 1):
        errs, warns = verify_plan(plan, asks, kb.guide, kb.labor, target_budget_usd=target)
        rounds.append({"round": attempt, "errors": errs, "warnings": warns})
        if not errs or attempt == MAX_REPAIRS:
            break
        try:
            repaired = await _call(
                system,
                ctx + f"\n\n=== YOUR PREVIOUS PLAN ===\n{json.dumps(plan)}\n\n"
                "=== VERIFIER ERRORS — return the full corrected plan ===\n" + "\n".join(errs),
            )
        except llm.LlmError as exc:
            logger.warning("pricing_plan_v2 repair call failed, keeping last plan: %s", exc)
            break
        if not isinstance(repaired, dict):
            break
        plan = repaired
    notes = plan.setdefault("internal_notes", [])
    notes += [{"issue": f"Unresolved check: {e}", "owner": "Sonja"} for e in rounds[-1]["errors"]]
    notes += [{"issue": w, "owner": "Sonja"} for w in rounds[-1]["warnings"] if w.startswith("unanchored")]
    amounts = compute(plan, kb.labor)["amounts"]
    for t in plan.get("tasks", []):
        if t.get("guide_id") == "6.1":
            note = {
                "issue": f"Media budget of {usd(amounts.get(t.get('task_id')))} is an assumption — "
                "confirm against the RFP / client media plan",
                "owner": "Sonja",
            }
            if note not in notes:
                notes.append(note)
    return plan, rounds


def _stamp(plan: dict, kb: PricingKb, tier: str, why: str) -> dict:
    plan["tier"] = tier
    plan["tier_basis"] = why  # code's reason; tier_rationale stays the LLM's line
    # Render must be pure and reproducible: keep exactly the KB values used.
    plan["kb_snapshot"] = {"verbatim": kb.guide["verbatim"], "labor": kb.labor}
    return plan


async def author_pricing_plan(
    rfp_text: str, asks: dict, kb: PricingKb, *, target_budget_usd: float | None = None
) -> tuple[dict, list[dict]]:
    tier, why = decide_tier(asks)
    ctx = _context(rfp_text, asks, kb, tier, why, target_budget_usd)
    plan = await _call(AUTHOR_PROMPT, ctx)
    plan, rounds = await _check_and_repair(
        plan, asks, kb, system=AUTHOR_PROMPT, ctx=ctx, target=target_budget_usd
    )
    return _stamp(plan, kb, tier, why), rounds


def plan_to_line_items(plan: dict, labor: dict) -> list[BudgetLineItem]:
    """Legacy ledger view: extended sums to the engine's term value (one_time + 12 x monthly)."""
    c = compute(plan, labor)
    items: list[BudgetLineItem] = []
    for t in plan.get("tasks", []):
        amt = c["amounts"].get(t["task_id"])
        billing = t.get("billing", "one_time")
        qty = float(t.get("quantity") or 1)
        notes = t.get("manual_reason")
        if amt is None:
            rate, extended = None, None
        elif billing == "monthly":
            qty, rate, extended = 12.0, amt, 12 * amt
        elif billing in ("per_event", "hourly"):  # rates, never summed
            qty, rate, extended = 1.0, amt, None
            notes = notes or ("per event" if billing == "per_event" else "hourly rate")
        else:
            rate, extended = round(amt / qty, 2), amt
        items.append(
            BudgetLineItem(
                id=t["task_id"],
                category=t.get("group") or "",
                description=t.get("deliverable") or "",
                unit=_UNIT.get(billing, "project"),
                quantity=qty,
                rate=rate,
                extended=extended,
                rate_source=f"{t['guide_id']} — {plan.get('tier')}" if t.get("guide_id") else "",
                notes=notes,
                line_item_type="client_passthrough" if t.get("guide_id") == "6.1" else "agency_fee",
                is_manual_fill=amt is None,
            )
        )
    return items


def _budget_from_plan(rfp_id: str, asks: dict, plan: dict, flags: list[str]) -> ProposalBudget:
    own = [x for x in asks.get("ceilings", []) if not x.get("shared_pool")]
    labor = (plan.get("kb_snapshot") or {}).get("labor") or {}
    flags = [*flags, *(f"[PRICING NOTE — {n.get('owner', '')}: {n.get('issue', '')}]" for n in plan.get("internal_notes", []))]
    flags.append(f"[PRICING NOTE — tier: {plan.get('tier')} — {plan.get('tier_basis', '')}]")
    return ProposalBudget(
        rfp_id=rfp_id,
        updated_at=datetime.now(timezone.utc).isoformat(),
        provider="pricing_plan_v2",
        budget_format="pricing_plan",
        pricing_tier=plan.get("tier"),
        rfp_budget_cap=sum(float(x["amount"]) for x in own) or None,
        rfp_budget_notes="",
        fee_structure="task-based fees from the pricing plan",
        line_items=plan_to_line_items(plan, labor),
        pricing_flags=flags,
        pricing_asks=asks,
        pricing_plan=plan,
    )


async def generate_pricing_plan_budget(
    rfp_id: str, rfp_text: str, *, target_budget_usd: float | None = None
) -> ProposalBudget:
    kb = await load_pricing_kb()
    asks, ask_errs = await extract_pricing_asks(rfp_text)
    plan, rounds = await author_pricing_plan(rfp_text, asks, kb, target_budget_usd=target_budget_usd)
    flags = [f"[PRICING FLAG: {e}]" for e in (*ask_errs, *rounds[-1]["errors"])]
    logger.info(
        "pricing_plan_v2 rfp_id=%s tier=%s rounds=%d unresolved=%d",
        rfp_id, plan.get("tier"), len(rounds), len(rounds[-1]["errors"]),
    )
    return _budget_from_plan(rfp_id, asks, plan, flags)


def render_pricing_plan_budget(budget: ProposalBudget) -> str:
    plan = budget.pricing_plan or {}
    snap = plan.get("kb_snapshot") or {}
    return render(plan, budget.pricing_asks or {}, {"verbatim": snap.get("verbatim") or {}}, snap.get("labor") or {})


async def edit_pricing_plan_from_chat(
    budget: ProposalBudget,
    *,
    instruction: str,
    rfp_text: str,
    target_budget_usd: float | None = None,
) -> tuple[ProposalBudget, str]:
    kb = await load_pricing_kb()
    asks = budget.pricing_asks or {}
    tier, why = decide_tier(asks)
    ctx = _context(rfp_text, asks, kb, tier, why, target_budget_usd)
    current = {
        k: v for k, v in (budget.pricing_plan or {}).items() if k not in {"tier", "tier_basis", "kb_snapshot"}
    }
    raw = await _call(
        EDIT_PROMPT,
        ctx + f"\n\n=== CURRENT PLAN ===\n{json.dumps(current)}\n\n=== USER INSTRUCTION ===\n{instruction}",
    )
    if not isinstance(raw, dict):
        raise ProposalError("Pricing plan LLM returned no plan", status_code=502)
    reply = str(raw.pop("reply", "") or "Updated the pricing plan.")
    plan, rounds = await _check_and_repair(raw, asks, kb, system=EDIT_PROMPT, ctx=ctx, target=target_budget_usd)
    flags = [f"[PRICING FLAG: {e}]" for e in rounds[-1]["errors"]]
    return _budget_from_plan(budget.rfp_id, asks, _stamp(plan, kb, tier, why), flags), reply
