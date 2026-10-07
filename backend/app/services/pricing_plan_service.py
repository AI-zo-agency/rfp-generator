"""Pricing plan: RFP asks -> LLM describes the work -> code prices and checks -> ProposalBudget.

The LLM never prices: it picks catalog items and estimates hours and POs for custom
work. All money math and checks live in pricing_plan_engine; this module only talks
to the LLM and the pricing docs and adapts the plan onto ProposalBudget.
Plans saved by the old band-based engine keep rendering through pricing_plan_legacy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services import llm, pricing_kb
from app.services import pricing_plan_legacy as legacy
from app.services.pricing_kb import PricingBook
from app.services.pricing_plan_engine import (
    budgets_from_asks,
    compute,
    cut_suggestions,
    internal_summary,
    price_plan,
    render,
    term_value,
    usd,
    verify_asks,
    verify_plan,
)
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

NODE = "pricing-plan-v2"
MAX_REPAIRS = 2
_UNIT = {"one_time": "project", "monthly": "month", "per_event": "event"}
_NOT_NOTES = ("implied limit", "RFP compensation outline")  # warnings the plan itself must raise or ignore

# Sonnet 5 adaptive thinking shares the completion budget; ASKS JSON for Form A-3
# RFPs is large — keep headroom well above the old 10k that truncated mid-string.
ASKS_MAX_TOKENS = 24000

ASKS_PROMPT = """You read an RFP and list how the buyer wants the COST / PRICE / BUDGET submitted.
Return ONE JSON object only — no markdown fences, no commentary, no trailing text:
{
 "compensation_outline": {"ref": "RFP section number + title of the cost/compensation section, or null",
                          "items": ["the RFP's own numbered items in that section, short, with their numbers"]},
 "ceilings": [{"label": "...", "amount": 950000, "scope": "total|annual|track", "track": "track name or null",
               "shared_pool": false, "quote": "verbatim"}],
 "implied_limits": [{"label": "...", "note": "e.g. procurement method statutorily capped", "quote": "verbatim"}],
 "client": {"name": "the buyer", "kind": "government|nonprofit|private", "quote": "verbatim proof of the kind, or null"},
 "contract_type_asked": {"value": "fixed_price|not_to_exceed|time_and_materials|cost_reimbursable|retainer|unit_price|other|null",
                         "quote": "verbatim or null"},
 "all_inclusive_pricing": {"value": false, "quote": "verbatim or null"},
 "buyer_form": null or {"name": "...", "may_modify": false,
     "columns": ["only the columns the bidder fills, e.g. QTY, PRICE, EXTENDED PRICE or one per department"],
     "rows": [{"row_id": "R1", "label": "exact row label", "unit": "HR|month|EA|...", "track": "track or null"}],
     "quote": "verbatim instruction"},
 "asks": [{"id": "K1", "kind": "cost_per_task|cost_per_deliverable|hourly_rates|hours_per_task|staffing_matrix|monthly_fee|unit_price|lump_sum|itemized_budget|invoice_content|payment_terms|media_costs|reimbursables|rate_lock|other",
           "required": true, "quote": "verbatim, <= 40 words", "locator": "section/page"}],
 "priced_scope": [{"id": "S1", "ref": "SOW ref", "item": "work item / deliverable the buyer names", "track": "track or null"}],
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
- client.kind: government for any public agency, city, county, state, school or university; nonprofit for a nonprofit; else private.
- Do not invent numbers."""

AUTHOR_PROMPT = """You are zö agency's pricing lead writing the budget / cost section of a proposal.
Sources: the RFP, the extracted pricing ASKS, and zö agency's pricing docs: the Pricing Book (client prices and
catalog), Rules and Wording, and Pricing Internal (settings, loaded rates, catalog costs, task library, pricing rules).

Return JSON only:
{
 "engagement_type": "fixed_quote|monthly_retainer|time_materials|time_deliverables|not_to_exceed|procurement",
 "engagement_basis": "one line: why this type; quote the RFP if it names a contract type",
 "client_name": "the buyer", "client_kind": "government|nonprofit|private", "term_months": 12,
 "tasks": [{"task_id": "A1", "group": "short group heading", "deliverable": "what the client gets",
            "scope_ids": ["S1"], "track": "exact ceiling/track label from ASKS or null",
            "billing": "one_time|monthly|per_event", "quantity": 1,
            "quantity_basis": "rfp|assumption  (only when quantity is not 1)",
            "quantity_quote": "verbatim from the RFP when quantity_basis is rfp",
            "catalog_code": "4g or null", "catalog_item": "the exact Pricing Book item name for that code",
            "build": null or {"hours": {"DS": 10}, "pos": {"Copywriter": 100}, "hard_cost": 0,
                              "basis": "the task library entry or your reasoning for the estimate"},
            "media_kind": null or "traditional" or "digital", "media_spend": null or number,
            "manual_reason": null or "why nothing prices this"}],
 "overhead": {"hours": {"PM": 12, "AM": 24, "LD": 4}, "basis": "..."},
 "hourly_roles": ["role names the RFP asks hourly rates for"],
 "fills": {"midpoint milestone": "...", "outside items": "..."},
 "form_fills": [{"row_id": "R1", "column": "exact column from ASKS buyer_form", "kind": "task_price|labor_rate|hours|extended|manual",
                 "task_ids": ["..."], "note": "..."}],
 "sections": [{"heading": "...", "body_md": "..."}],
 "internal_notes": [{"issue": "...", "owner": "Sonja|Writer"}]
}

PRICING RULES
- You describe the work; code sets every price. You never type a price, a total, a rate or a percentage.
- Catalog: when an RFP item matches a Pricing Book item, set catalog_code and the exact catalog_item name, with quantity
  (months for a monthly item). Give it no build: code copies its hours and costs and sells it at the Pricing Book price.
- Custom work (no catalog match): give a build. hours are in-house hours by role key from Pricing Internal (DS, PD, WD, DG,
  AM, PM, LD); pos are POs by type (Creative Director, Strategist, Copywriter, Photo & Video, Other POs) in dollars;
  hard_cost is tools, stock, travel at cost. Creative direction, strategy and copy are always POs, never hours.
  Start from the task library. Every build needs a basis. Estimate honestly: code checks hours against price and floor.
- overhead: project management, account management and Agency Director oversight (PM, AM, LD hours) for the whole
  engagement. They are never separate lines. Required whenever there is custom work or more than one priced task.
- Quantities (months, posts, pages, events): quantity_basis "rfp" with a verbatim quantity_quote when the RFP states the
  count, otherwise "assumption" (code flags it for review).
- engagement_type: pick from the RFP's scope. Defined deliverables with an end date are fixed_quote (the default); steady
  monthly work is monthly_retainer; swag and print runs are procurement. zö agency is value-based: price each item or phase.
  If the RFP requires time and materials or not-to-exceed, still build by item or phase, set the type, and say so in internal_notes.
- billing: one_time = total over the term (quantity x item); monthly = a recurring monthly fee (quantity 1; term_months sets
  the months); per_event = a price per occurrence. term_months is the contract term in months.
- Paid media: traditional media is a task with media_kind "traditional" and media_spend = the client's total media budget
  (code books the commission and the placement split; add a build for the placement hours). Digital media is a monthly task with
  media_kind "digital" and media_spend = monthly ad spend (code sets the management fee). Ad spend runs on the client's
  card and is never itself a task.
- Scope no catalog item or task library entry covers: still build it with a named vendor PO, and say so in internal_notes.
  Only use manual_reason when nothing can be estimated.
- Every ASKS priced_scope id must appear in some task's scope_ids.
- Ceilings and tracks: set each task's track to the exact ceiling label when the RFP has several; code fits each to its budget.
  shared_pool ceilings are not ours: size the bid to the scope and do not claim the pool.
- Rates: when the RFP asks for rates, list the role names in hourly_roles; code enters the same blended rate on every one.
  Forms that ask for hours or extended price per task: use kind "hours" or "extended"; code leaves them for Sonja. Forms that ask
  for a price use kind "task_price" with the task_ids; forms that ask for an hourly rate use "labor_rate".
- Wording: code inserts approved wording blocks. Fill their slots in "fills": midpoint milestone (fixed quote), client contact and
  cap (not to exceed), travel rule, event or meeting (travel), and "outside items" (the list of what is outside the price,
  such as ad spend on the client's card, printing, new shoots; write the client's name where it applies).

RFP OVERRIDES
- If ASKS all_inclusive_pricing is true, outside-the-price must not add separate expenses; say prices are all-inclusive.
- Honor rate locks, exclusions, invoice content and payment terms stated in the RFP.

WRITING RULES
- When ASKS compensation_outline has items, the section headings MUST mirror that numbering and order
  (e.g. "1. Compensation Contingent on Approved Deliverables"); answer each item under its own number.
  Otherwise use: Investment Summary, Cost by Item, Rates (if asked), Media (if any), Terms.
- body_md contains NO dollar figures, NO percentages of money, NO hours, NO roles. Money appears only through tokens:
  block tokens (own line, each used at most once per argument):
    {{TASK_TABLE}} or {{TASK_TABLE:<track>}}  {{RATE_TABLE}}  {{FORM}}  {{MEDIA_SPLIT}}  {{VERBATIM:<key>}}
  inline tokens: {{TOTAL}} {{TOTAL:<track>}} {{AMT:<task_id>}} {{RATE:<role>}}
  <track> is the exact track/ceiling label. {{TOTAL}} = term value (one-time work plus the term's monthly fees) and is the
  same number the task table's total row shows.
- Verbatim blocks, required: {{VERBATIM:billing}} (invoicing and terms for the engagement), {{VERBATIM:outside}}
  (what is outside the price), {{VERBATIM:changes}} (change orders). Optional: {{VERBATIM:travel}}, {{VERBATIM:rates}}.
  Traditional media requires {{MEDIA_SPLIT}}.
- Answer every required ask explicitly (invoice content, payment terms, rate lock, exclusions...).
- Client-facing prose says what the client gets at the level of the deliverable. No internal names (Sonja, Ella), no
  vendors, no margins, costs, hours, staffing or "Pricing Book". Put open questions, assumptions and approvals in internal_notes.
- Every ASKS implied_limits entry must be raised in internal_notes with what it means for the total.
- Short, plain, confident sentences. No filler. No em dashes."""

EDIT_PROMPT = """You update an existing zö pricing plan to follow the user's instruction.
Return the FULL updated plan JSON (same schema as the plan you are given) plus a top-level
"reply": one or two sentences telling the user what changed.
- Change only what the instruction asks. Keep task_ids stable for unchanged tasks.
- Every pricing and writing rule below still applies. Code reprices after your change.
- If the instruction asks for something the rules forbid (a price you set by hand, a rate other than the blended
  rate, hours or costs in the client text, a dollar figure in prose), do not do it. Explain why in "reply".

""" + AUTHOR_PROMPT


async def load_pricing_kb() -> PricingBook:
    """The newest complete, valid set of pricing docs (raises ProposalError 424 when there is none)."""
    return (await pricing_kb.load_pricing_book()).book


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


def _asks_rfp_context(rfp_text: str) -> str:
    """Cost/pricing windows only — full 70-page RFPs blow the ASKS JSON budget."""
    from app.services.proposal_rfp_excerpt import budget_and_cost_excerpt

    body = (rfp_text or "").strip()
    if not body:
        return ""
    excerpt = budget_and_cost_excerpt(body, max_chars=48_000)
    # Thin excerpt → fall back to full text (small RFPs / odd wording).
    min_keep = min(8_000, max(1, len(body) // 4))
    if excerpt and len(excerpt) >= min_keep:
        logger.info(
            "pricing_asks_rfp_context excerpt_chars=%d full_chars=%d",
            len(excerpt),
            len(body),
        )
        return excerpt
    logger.info(
        "pricing_asks_rfp_context using_full_rfp excerpt_chars=%d full_chars=%d",
        len(excerpt or ""),
        len(body),
    )
    return body


async def extract_pricing_asks(rfp_text: str) -> tuple[dict, list[str]]:
    ctx = _asks_rfp_context(rfp_text)
    asks = await _call(
        ASKS_PROMPT, f"=== RFP (cost / pricing excerpts) ===\n{ctx}", ASKS_MAX_TOKENS
    )
    errs = verify_asks(asks, rfp_text)
    if errs:
        asks = await _call(
            ASKS_PROMPT,
            f"=== RFP (cost / pricing excerpts) ===\n{ctx}\n\n"
            f"=== YOUR PREVIOUS JSON ===\n{json.dumps(asks)}\n\n"
            "=== FIX THESE (quote exactly or drop the item) ===\n" + "\n".join(errs),
            ASKS_MAX_TOKENS,
        )
        errs = verify_asks(asks, rfp_text)
    return asks, errs


def _context(rfp_text: str, asks: dict, book: PricingBook, budgets: dict) -> str:
    fit = (
        f"=== BUDGET TO FIT (code prices inside it; scopes are the track labels) ===\n{json.dumps(budgets)}\n\n"
        if budgets else "=== BUDGET: none printed. Code prices custom work near cost / 0.40. ===\n\n"
    )
    return (
        f"{fit}=== PRICING ASKS (extracted, verified) ===\n{json.dumps(asks, indent=1)}\n\n"
        f"=== PRICING BOOK ({book.version}) ===\n{book.book_md}\n\n"
        f"=== RULES AND WORDING ===\n{book.rules_md}\n\n"
        f"=== PRICING INTERNAL (confidential: never in client text) ===\n{book.internal_md}\n\n"
        f"=== RFP ===\n{rfp_text}"
    )


def _check(plan: dict, asks: dict, book: PricingBook, budgets: dict, rfp_text: str) -> tuple[dict, list[str], list[str]]:
    """Price the plan, then verify it. A malformed plan comes back as an error for the repair loop, never an exception."""
    try:
        report = price_plan(plan, book, budgets)
        errs, warns = verify_plan(plan, asks, book, budgets=budgets, rfp_text=rfp_text, report=report)
    except (TypeError, ValueError, KeyError, AttributeError, ArithmeticError) as exc:
        return {"no_fit": {}}, [f"plan is malformed ({type(exc).__name__}: {exc}); return the full plan in the schema"], []
    return report, errs, warns


async def _check_and_repair(
    plan: dict, asks: dict, book: PricingBook, *, system: str, ctx: str, budgets: dict, rfp_text: str
) -> tuple[dict, list[dict]]:
    if not isinstance(plan, dict):
        raise ProposalError("Pricing plan LLM returned no plan", status_code=502)
    rounds: list[dict] = []
    for attempt in range(MAX_REPAIRS + 1):
        report, errs, warns = _check(plan, asks, book, budgets, rfp_text)
        rounds.append({"round": attempt, "errors": errs, "warnings": warns, "report": report})
        if not errs or attempt == MAX_REPAIRS:
            break
        try:
            repaired = await _call(
                system,
                ctx + f"\n\n=== YOUR PREVIOUS PLAN ===\n{json.dumps(plan)}\n\n"
                "=== VERIFIER ERRORS — return the full corrected plan ===\n" + "\n".join(errs),
            )
        except llm.LlmError as exc:
            logger.warning("pricing_plan repair call failed, keeping last plan: %s", exc)
            break
        if not isinstance(repaired, dict):
            break
        plan = repaired
    last = rounds[-1]
    notes = plan.setdefault("internal_notes", [])
    notes += [{"issue": f"Unresolved check: {e}", "owner": "Sonja"} for e in last["errors"]]
    notes += [{"issue": w, "owner": "Sonja"} for w in last["warnings"] if not w.startswith(_NOT_NOTES)]
    try:
        _price_notes(plan, book, last["report"], notes)
    except (TypeError, ValueError, KeyError, AttributeError, ArithmeticError) as exc:  # already in the errors
        logger.warning("pricing_plan notes skipped: %s", exc)
    return plan, rounds


def _price_notes(plan: dict, book: PricingBook, report: dict, notes: list[dict]) -> None:
    """Internal notes the code can write itself: scope cuts when the floor is over budget, media assumptions."""
    for key, info in report.get("no_fit", {}).items():
        cuts = cut_suggestions(plan, book, info["floor_total"] - info["budget"], key)
        notes.append({"issue": f"Floor price {usd(info['floor_total'])} is over the budget {usd(info['budget'])}. "
                               f"Cuts that would fit: {'; '.join(cuts) or 'none found'}", "owner": "Sonja"})
    c = compute(plan, book)
    for t in plan.get("tasks", []):
        if t.get("media_kind") and t.get("task_id") in c["amounts"]:
            spend = usd(float(t.get("media_spend") or 0))
            if not any(spend in n.get("issue", "") for n in notes):
                notes.append({"issue": f"Media budget of {spend} is an assumption — confirm against the RFP / client media plan",
                              "owner": "Sonja"})


def _stamp(plan: dict, book: PricingBook) -> dict:
    """Keep exactly the pricing values used, so render and re-pricing stay reproducible without the KB."""
    codes = {t["catalog_code"] for t in plan.get("tasks", []) if t.get("catalog_code")}
    plan["kb_snapshot"] = book.snapshot(codes)
    plan["pricing_version"] = book.version
    plan["internal_summary"] = internal_summary(plan, book)
    return plan


async def author_pricing_plan(
    rfp_text: str, asks: dict, book: PricingBook, *, target_budget_usd: float | None = None
) -> tuple[dict, list[dict]]:
    budgets = budgets_from_asks(asks, target_budget_usd)
    ctx = _context(rfp_text, asks, book, budgets)
    plan = await _call(AUTHOR_PROMPT, ctx)
    plan, rounds = await _check_and_repair(
        plan, asks, book, system=AUTHOR_PROMPT, ctx=ctx, budgets=budgets, rfp_text=rfp_text
    )
    return _stamp(plan, book), rounds


def plan_to_line_items(plan: dict, book: PricingBook) -> list[BudgetLineItem]:
    """Legacy ledger view: extended sums to the engine's term value (one_time + months x monthly)."""
    c = compute(plan, book)
    items: list[BudgetLineItem] = []
    for t in plan.get("tasks", []):
        amt = c["amounts"].get(t["task_id"])
        billing = t.get("billing", "one_time")
        qty = float(t.get("quantity") or 1)
        notes = t.get("manual_reason")
        if amt is None:
            rate, extended = None, None
        elif billing == "monthly":
            qty, rate, extended = float(c["months"]), amt, c["months"] * amt
        elif billing == "per_event":  # a rate, never summed
            qty, rate, extended = 1.0, amt, None
            notes = notes or "per event"
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
                rate_source=f"Catalog {t['catalog_code']}" if t.get("catalog_code") else "Priced from the work",
                notes=notes,
                line_item_type="client_passthrough" if t.get("media_kind") == "traditional" else "agency_fee",
                is_manual_fill=amt is None,
            )
        )
    return items


def _budget_from_plan(rfp_id: str, asks: dict, plan: dict, flags: list[str], book: PricingBook) -> ProposalBudget:
    own = [x for x in asks.get("ceilings", []) if not x.get("shared_pool")]
    flags = [*flags, *(f"[PRICING NOTE — {n.get('owner', '')}: {n.get('issue', '')}]" for n in plan.get("internal_notes", []))]
    # Flags are shown to everyone who opens the proposal: no margins, costs or hours here.
    # Those stay in plan["internal_summary"].
    flags.append(f"[PRICING NOTE — priced with Pricing {book.version}: {usd(term_value(compute(plan, book)))} over the term]")
    return ProposalBudget(
        rfp_id=rfp_id,
        updated_at=datetime.now(timezone.utc).isoformat(),
        provider="pricing_plan_v2",
        budget_format="pricing_plan",
        pricing_tier=None,
        rfp_budget_cap=sum(float(x["amount"]) for x in own) or None,
        rfp_budget_notes="",
        fee_structure="priced by item or phase from the work behind each price",
        line_items=plan_to_line_items(plan, book),
        pricing_flags=flags,
        pricing_asks=asks,
        pricing_plan=plan,
    )


async def generate_pricing_plan_budget(
    rfp_id: str, rfp_text: str, *, target_budget_usd: float | None = None
) -> ProposalBudget:
    book = await load_pricing_kb()
    asks, ask_errs = await extract_pricing_asks(rfp_text)
    plan, rounds = await author_pricing_plan(rfp_text, asks, book, target_budget_usd=target_budget_usd)
    flags = [f"[PRICING FLAG: {e}]" for e in (*ask_errs, *rounds[-1]["errors"])]
    logger.info(
        "pricing_plan rfp_id=%s version=%s rounds=%d unresolved=%d",
        rfp_id, book.version, len(rounds), len(rounds[-1]["errors"]),
    )
    return _budget_from_plan(rfp_id, asks, plan, flags, book)


def render_pricing_plan_budget(budget: ProposalBudget) -> str:
    plan = budget.pricing_plan or {}
    snap = plan.get("kb_snapshot") or {}
    asks = budget.pricing_asks or {}
    if legacy.is_legacy(plan):
        return legacy.render(plan, asks, {"verbatim": snap.get("verbatim") or {}}, snap.get("labor") or {})
    return render(plan, asks, PricingBook.from_snapshot(snap))


def plan_term_value(plan: dict, track: str | None = None, media: bool | None = None) -> float:
    """Term value of a saved plan (new or legacy). `media` limits it to the traditional-media pass-through."""
    snap = plan.get("kb_snapshot") or {}
    if legacy.is_legacy(plan):
        return legacy.term_value(legacy.compute(plan, snap.get("labor") or {}), track, "6.1" if media else None)
    return term_value(compute(plan, PricingBook.from_snapshot(snap)), track, media)


async def edit_pricing_plan_from_chat(
    budget: ProposalBudget,
    *,
    instruction: str,
    rfp_text: str,
    target_budget_usd: float | None = None,
) -> tuple[ProposalBudget, str]:
    saved = budget.pricing_plan or {}
    if legacy.is_legacy(saved):
        raise ProposalError(
            "This budget was priced with the old pricing guide. Regenerate the budget to price it with the "
            "current pricing docs.", status_code=409,
        )
    book = await load_pricing_kb()
    if book.version != saved["kb_snapshot"]["version"]:
        raise ProposalError(
            f"This budget was priced with Pricing {saved['kb_snapshot']['version']}, and Pricing {book.version} is now "
            "live. Regenerate the budget to price it with the new docs.", status_code=409,
        )
    asks = budget.pricing_asks or {}
    budgets = budgets_from_asks(asks, target_budget_usd)
    ctx = _context(rfp_text, asks, book, budgets)
    current = {k: v for k, v in saved.items() if k not in {"kb_snapshot", "internal_summary", "pricing", "pricing_version"}}
    raw = await _call(
        EDIT_PROMPT,
        ctx + f"\n\n=== CURRENT PLAN ===\n{json.dumps(current)}\n\n=== USER INSTRUCTION ===\n{instruction}",
    )
    if not isinstance(raw, dict):
        raise ProposalError("Pricing plan LLM returned no plan", status_code=502)
    reply = str(raw.pop("reply", "") or "Updated the pricing plan.")
    plan, rounds = await _check_and_repair(
        raw, asks, book, system=EDIT_PROMPT, ctx=ctx, budgets=budgets, rfp_text=rfp_text
    )
    flags = [f"[PRICING FLAG: {e}]" for e in rounds[-1]["errors"]]
    return _budget_from_plan(budget.rfp_id, asks, _stamp(plan, book), flags, book), reply
