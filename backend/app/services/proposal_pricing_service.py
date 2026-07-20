"""Stage 3 budget: 00_Guide_Pricing + Stage 1/2 context + RFP excerpt."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import (
    BudgetLineItem,
    PricingTier,
    ProposalBudget,
    ProposalResearchCache,
    VerifiedRate,
)
from app.models.rfp import RfpRecord
from app.services import llm, supermemory
from app.services.llm import LlmError
from app.services.proposal_fee_justification import generate_fee_justification_memo
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_validation import (
    parse_budget_extras,
    reconcile_proposal_budget,
)
from app.services.proposal_common import ProposalError, load_rfp_for_proposal
from app.services.proposal_knowledge_base_tools import search_knowledge_base
from app.services.proposal_repository import get_research_cache, save_research_cache

logger = logging.getLogger(__name__)

GUIDE_SEARCH_CHAR_LIMIT = 24_000

STAGE3_BUDGET_PROMPT = """You are zö agency's Stage 3 Budget assistant. Build a complete, defensible budget using ONLY:
- The Pricing Guide menu below (tier ranges)
- 00_Guide_Pricing excerpts from the knowledge base
- Stage 1 Go/No-Go analysis, Stage 2 structural map, and RFP excerpt
- 06_WON / 07_FIN proposal excerpts ONLY for budget page format or burdened hourly rates when the RFP requires personnel loading

Do not invent numbers. Every amount must trace to a Pricing Guide line item at the selected tier or an explicit KB excerpt.

=== PROCESS (follow in order) ===

PHASE 1 — Extract five signals from the RFP and prior stages:
1. Budget ceiling (hard cap if stated — stay under it)
2. Cost weight in scoring → tier: 25%+ = Low, 15–20% = Average (default), 10% or less = High
3. Budget format: phased | personnel_loading | service_menu
4. Deliverables from Stage 2 → each becomes a line item
5. Travel — add direct expenses line if zö is out of region

PHASE 2 — Pick ONE pricing tier (Low / Average / High) for the entire proposal.

PHASE 3 — Map every deliverable to a Pricing Guide line item:

Category 01 — Discovery & Research
- 1.1 Stakeholder Interviews (Avg: $6,000–$8,000)
- 1.2 Community Listening Sessions (Avg: $12,000–$18,000)

Category 02 — Strategy
- 2.1 Brand Foundation Strategy / Messaging Architecture (Avg: $8,000–$14,000)
- 2.2 Competitive Positioning Strategy (Avg: $3,750–$5,500)
- 2.3 Campaign Strategy Development (Avg: $5,500–$9,000)
- 2.4 Strategy & Creative Foundation Bundle (Avg: $14,000–$18,000)
- 2.5 Content Strategy & Storytelling Framework (Avg: $3,000–$4,500)
- 2.6 KPI Development & Measurement Framework (Avg: $3,750–$5,500)

Category 03 — Brand Identity & Creative
- 3.1 Campaign Brand Development (Avg: $2,900–$3,800)
- 3.2 Program/Initiative Brand Package (Avg: $3,200–$4,500)
- 3.3 Department/Service Brand Guidelines (Avg: $4,800–$6,500)
- 3.4 Brand Identity Evolution & Design (Avg: $6,000–$9,000)
- 3.5 Campaign Concept Development (Avg: $4,500–$6,500)
- 3.6 Policy Communication Package (Avg: $2,500–$3,500)

Category 04 — Content Creation
- 4.1 Custom Graphic Design per asset (Avg: $275–$450)
- 4.2 Infographic Design (Avg: $575–$850)
- 4.3 Print Collateral Design (Avg: $925–$1,400)
- 4.4 Email Newsletter Design & Setup (Avg: $975–$1,500)
- 4.5 Monthly Social Media Content Package (Avg: $2,900–$3,800 / 16 posts)
- 4.6 Monthly Blog Package (Avg: $2,200–$3,200 / 4 posts)

Category 05 — Digital Marketing
- 5.1 Digital Campaign Strategy (Avg: $2,200–$3,500)
- 5.2 Landing Page Design & Development (Avg: $2,700–$4,500)
- 5.3 Monthly Social Media Management 3 platforms (Avg: $3,200–$4,800)
- 5.4 Monthly Digital Advertising Management (Avg: $2,500–$4,500)
- 5.5 Integrated Digital Media & Management Bundle (Avg: $55,000–$75,000)

Category 06 — Media Planning & Placement
- 6.1 Traditional Media: 85/15 commission (85% placements, 15% zö). Client invoiced at net.
  For commission-model RFPs: tag media placement rows lineItemType=client_passthrough;
  tag agency commission / PM / strategy rows lineItemType=agency_fee.
  agencyRevenueEstimate = ONLY agency_fee rows + directExpensesTotal — NEVER include pass-through media.

PHASE 3b — Commission / pass-through model (when RFP uses media commission or net invoicing):
- clientMediaPassthrough = sum of lineItems where lineItemType=client_passthrough
- agencyFeeSubtotal = sum of lineItems where lineItemType=agency_fee
- agencyRevenueEstimate = agencyFeeSubtotal + directExpensesTotal (zö's actual income)
- totalClientInvoicing = lineItemSum + directExpensesTotal (what client pays in total)
- Budget Summary MUST label these separately — never call pass-through media "agency revenue"
- optionTermNotes multi-year math uses agencyRevenueEstimate base only, not totalClientInvoicing
- Example (85/15): $250,000 annual media at 15% → clientMediaPassthrough=250000, commissionRate=0.15,
  agencyFeeSubtotal=37500, agencyRevenueEstimate=37500 (NOT zero, NOT equal to pass-through total)
- Populate commissionRate AND clientMediaPassthrough whenever commission applies — reconcile math depends on them

ZERO-DOLLAR PROHIBITION (submission disqualifier):
- NEVER return agencyRevenueEstimate = 0 or null when commission or agency fees apply.
- NEVER show "$0" for "Agency revenue estimate" or "Line item table total" when the RFP is commission-based —
  the commission dollar amount IS the agency revenue (rate × pass-through or sum of agency_fee rows).
- lineItemSum may be large (mostly pass-through media); agencyRevenueEstimate is still the fee income only.
- lumpSumTotal and optionTermNotes MUST cite the same positive annual agency fee as agencyRevenueEstimate.
- If estimated annual media spend is in RFP/Stage 1, use it for clientMediaPassthrough and compute commission.

STAFF HOURS (when RFP Section D requires hours and billing rates):
- Add a "## Staff Hours" table: Role | Task/Scope line | Hours/year | Loaded rate | Extended
- Use verifiedRates from KB when available; otherwise [PRICING FLAG: Sonja to approve burdened rates]
- Commission-model RFPs STILL need this transparency table — commission is total compensation but evaluators require hours

Category 07 — Implementation & Launch
- 7.1 Pilot Social Media Campaign (Avg: $6,000–$9,000)
- 7.2 Digital Advertising Setup & Initial Run (Avg: $5,250–$7,500)
- 7.3 Influencer Collaboration & Community Engagement (Avg: $2,250–$4,500)
- 7.4 Launch Event Coordination (Avg: $1,500–$3,500)

Category 08 — Measurement & Reporting
- 8.1 Measurement & Analytics per campaign (Avg: $4,000–$6,500)

Category 09 — Account & Project Management
- 9.1 Project Management short projects 3–6 months (Avg: $7,500–$12,000)
- 9.2 Project Management campaign-specific (Avg: $5,000–$8,500)
- Rule: PM must be 5–8% of total project investment.

Category 10 — Strategic Deliverables
- 10.1 Strategic Plan Document Production (Avg: $6,000–$9,000)
- 10.2 Brand Messaging Toolkit (Avg: $4,500–$7,000)
- 10.3 Social Media Playbook (Avg: $4,500–$7,500)
- 10.4 Template & Guideline Development (Avg: $3,000–$5,500)
- 10.5 Implementation Roadmap bundle (Avg: $12,000–$18,000)

Use Low / High tier ranges from 00_Guide_Pricing KB excerpts when tier is not Average.
Anything not in this menu → pricingFlags: [PRICING FLAG: (description) — outside approved parameters, Sonja review required]

PHASE 4 — Stress-test before finalizing:
- Total sustains 50% wages / 30% G&A / 20% profit
- Under RFP ceiling (scope down if over — do not price below floor)
- Leave 15–20% room for scope expansion
- PM is 5–8% of total

PHASE 5 — Budget page format (match THIS RFP — titles/fields from the solicitation):
- blended_rate_form: RFP provides a Pricing/Cost Proposal Form with ONE hourly, ONE monthly,
  and ONE annual rate (annual = monthly × 12). Fill formHourlyRate / formMonthlyRate /
  formAnnualRate explicitly. Keep detailed line items only as supporting rationale AFTER the form.
- phased: Phase 1/2/3 subtotals + project total
- personnel_loading: Team Member / Classification / Hourly Rate / Hours / Subtotal + NTE + Direct Expenses
- service_menu: per-unit or per-project rates by category

If the RFP form asks for three blended rates, budgetFormat MUST be blended_rate_form — do not
substitute a 17-line personnel table as the primary answer.

INVERSE COST SCORING (when RFP awards max cost points to lowest responsive price):
- Never claim matching the budget ceiling maximizes cost score.
- qualifyingLanguage must acknowledge lowest-price-wins math if bid is at/near ceiling.
- Sum ALL cost-related criteria points (e.g. Cost Points Conversion + Price Reasonableness).

SEPARATE BUDGET ATTACHMENT (Attachment 01 / Excel worksheet):
- scopeSummary and qualifyingLanguage must state the official worksheet is the pricing submission.
- Line items in JSON support the attachment; narrative budget section is cover/rationale only.

QUOTATION FORM ALTERATION (submission disqualifier when RFP says so):
- If the RFP states that altering or departing from the Quotation/Pricing Proposal Form
  disqualifies the bid: NEVER output Section A/B/C/D substitutes or extra clauses on the form.
- Put hourly/monthly/annual (and amount-in-words placeholders) in formHourlyRate fields only.
- qualifyingLanguage, commission model, scope protection, and line items belong in supporting
  rationale AFTER the verbatim form — not labeled as sections of the official form.

PHASE 6 — qualifyingLanguage MUST include all four blocks:
Investment Framing, Scope Protection, Reimbursable Expenses, Revision Rounds (use KB guide wording when present).
qualifyingLanguage MUST use the SAME pricingTier selected in PHASE 2 — never mention a different tier as "baseline."

MATH (mandatory — verify before returning):
1. For EACH lineItem: extended MUST equal rate × quantity (recalculate if needed).
2. Sum every lineItems.extended row explicitly — that subtotal is lineItemSum (ground truth).
3. Tag each lineItem with lineItemType: agency_fee | client_passthrough | direct_expense.
4. agencyRevenueEstimate = agency fee income ONLY (agency_fee rows + directExpensesTotal).
   For commission models: NEVER include client_passthrough rows in agencyRevenueEstimate.
5. totalClientInvoicing = lineItemSum + directExpensesTotal when pass-through media is present.
6. lumpSumTotal MUST equal agencyRevenueEstimate when RFP requires lump sum + hourly.
7. optionTermNotes MUST use agencyRevenueEstimate as the base-year agency fee figure.
8. Do NOT leave pricingFlags describing math discrepancies — fix the numbers instead.
9. pricingFlags are ONLY for items requiring Sonja/human review (out-of-guide scope, missing KB).
   NEVER put compliance or qualification [VERIFY: …] tags in pricingFlags — those belong in proposal narrative sections, not the budget object.
10. Final check: agencyRevenueEstimate > 0 whenever commissionRate or agency_fee line items exist — reject your own output if zero.

Return ONLY JSON:
{
  "rfpBudgetCap": number|null,
  "rfpBudgetNotes": "string",
  "feeStructure": "string",
  "pricingTier": "Low|Average|High",
  "budgetFormat": "blended_rate_form|phased|personnel_loading|service_menu",
  "formHourlyRate": number|null,
  "formMonthlyRate": number|null,
  "formAnnualRate": number|null,
  "formRateNotes": "string — how the three form rates were derived",
  "commissionModel": "string|null",
  "commissionRate": number|null,
  "lumpSumTotal": number|null,
  "directExpensesTotal": number|null,
  "lineItemSum": number|null,
  "agencyFeeSubtotal": number|null,
  "clientMediaPassthrough": number|null,
  "totalClientInvoicing": number|null,
  "verifiedRates": [{"personName","role","hourlyRate","source"}],
  "lineItems": [{"id","category","description","lineItemType","namedPerson","roleTitle","unit","quantity","rate","extended","rateSource","notes"}],
  "tiers": [],
  "recommendedTierId": null,
  "agencyRevenueEstimate": number|null,
  "pricingFlags": ["string"],
  "qualifyingLanguage": "string or {investmentFraming, scopeProtection, reimbursableExpenses, revisionRounds}",
  "scopeAdjustments": ["string"],
  "scopeSummary": "string",
  "designBrief": "string",
  "optionTermNotes": "string",
  "mediaSpendNotes": "string",
  "confidence": 0-100
}

lineItems must be a flat array (one row per line). Do not back-fill to the budget ceiling.

rateSource on each lineItem should cite the guide menu item (e.g. "5.3 — 00_Guide_Pricing Average tier")."""


def _stage_one_text(rfp: RfpRecord) -> tuple[str, bool]:
    analysis = rfp.go_no_go_analysis or {}
    if not analysis:
        return "(Stage 1 Go/No-Go not run — run fit analysis first.)", False
    parts: list[str] = []
    if analysis.get("summary"):
        parts.append(f"Summary: {analysis['summary']}")
    report = analysis.get("stageOneReport") or analysis.get("stage_one_report")
    if report:
        parts.append(str(report))
    for row in analysis.get("decisionMatrix") or []:
        if isinstance(row, dict):
            parts.append(f"{row.get('dimension', '')}: {row.get('notes', '')}")
    text = "\n".join(parts).strip()
    return text or "(Stage 1 complete but no report text.)", bool(text)


def _structural_map_text(
    research: ProposalResearchCache | None,
) -> tuple[str, bool]:
    if not research or not research.rfp_sections:
        return (
            "(Stage 2 not ready — run full proposal or Sections 1–3 KB first.)",
            False,
        )
    lines: list[str] = []
    for section in research.rfp_sections[:20]:
        reqs = [r.strip() for r in section.requirements if r and r.strip()]
        weight = (
            f" (eval {section.evaluation_weight}%)"
            if section.evaluation_weight is not None
            else ""
        )
        title = section.title or section.id
        lines.append(f"- {title}{weight}: {', '.join(reqs[:10]) or '(pending)'}")
    text = "\n".join(lines).strip()
    return text, bool(text)


async def _fetch_guide_context(rfp: RfpRecord, stage_two: str) -> tuple[str, list[str]]:
    """Retrieve 00_Guide_Pricing from Supermemory (pricing + reference categories)."""
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    scope_hint = stage_two[:200] if stage_two else (rfp.title or "")
    queries = [
        "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management contingency qualifying language",
        f"00_Guide_Pricing {rfp.client or ''} {scope_hint[:150]}".strip(),
    ]

    chunks: list[str] = []
    sources: list[str] = []
    for query in queries:
        for category in ("reference", "pricing"):
            text, srcs = await search_knowledge_base(
                query,
                limit=8,
                category=category,
                max_chars=GUIDE_SEARCH_CHAR_LIMIT // 2,
            )
            if text and not text.startswith("("):
                chunks.append(text)
            for src in srcs:
                if src not in sources:
                    sources.append(src)

    combined = "\n\n---\n\n".join(chunks)[:GUIDE_SEARCH_CHAR_LIMIT]
    return combined or "(No 00_Guide_Pricing content in KB — ingest pricing guide.)", sources


def _parse_line_items(raw_items: Any) -> list[BudgetLineItem]:
    if not isinstance(raw_items, list):
        return []
    items: list[BudgetLineItem] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        description = (
            item.get("description")
            or item.get("roleTitle")
            or item.get("role")
            or item.get("name")
            or "Budget line item"
        )
        try:
            items.append(
                BudgetLineItem.model_validate(
                    {
                        **item,
                        "id": item.get("id") or f"li-{index + 1}",
                        "description": str(description),
                        "category": item.get("category") or "labor",
                        "namedPerson": item.get("namedPerson") or item.get("person"),
                        "roleTitle": item.get("roleTitle") or item.get("role"),
                        "rate": item.get("rate") if item.get("rate") is not None else item.get("hourlyRate"),
                        "quantity": item.get("quantity") if item.get("quantity") is not None else item.get("hours"),
                        "extended": item.get("extended") if item.get("extended") is not None else item.get("subtotal"),
                        "unit": item.get("unit") or ("hours" if item.get("hours") else "flat"),
                    }
                )
            )
        except Exception:
            logger.debug("Skipped unparseable line item", exc_info=True)
    return items


def _normalize_qualifying_language(raw: Any) -> str:
    if isinstance(raw, dict):
        labels = {
            "investmentFraming": "Investment Framing",
            "scopeProtection": "Scope Protection",
            "reimbursableExpenses": "Reimbursable Expenses",
            "revisionRounds": "Revision Rounds",
        }
        parts = []
        for key, value in raw.items():
            if value and str(value).strip():
                label = labels.get(key, key)
                parts.append(f"{label}\n{str(value).strip()}")
        return "\n\n".join(parts)
    return str(raw or "").strip()


def _parse_tiers(raw_tiers: Any) -> list[PricingTier]:
    if not isinstance(raw_tiers, list):
        return []
    tiers: list[PricingTier] = []
    for index, item in enumerate(raw_tiers):
        if not isinstance(item, dict):
            continue
        try:
            tiers.append(
                PricingTier.model_validate(
                    {**item, "id": item.get("id") or f"tier-{index + 1}"}
                )
            )
        except Exception:
            continue
    return tiers


def _parse_verified_rates(raw_rates: Any) -> list[VerifiedRate]:
    if not isinstance(raw_rates, list):
        return []
    rates: list[VerifiedRate] = []
    for item in raw_rates:
        if not isinstance(item, dict):
            continue
        try:
            rates.append(VerifiedRate.model_validate(item))
        except Exception:
            continue
    return rates


def _parse_budget_cap(raw_cap: Any) -> float | None:
    if isinstance(raw_cap, (int, float)) and float(raw_cap) > 0:
        return float(raw_cap)
    return None


async def generate_proposal_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Stage 3 budget: Stage 1 + Stage 2 + 00_Guide_Pricing + RFP excerpt → single LLM pass."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp, _content, rfp_context = load_rfp_for_proposal(rfp_id)
    prior_research = get_research_cache(rfp_id)

    stage_one, stage_one_ready = _stage_one_text(rfp)
    stage_two, stage_two_ready = _structural_map_text(prior_research)
    guide_text, kb_sources = await _fetch_guide_context(rfp, stage_two)

    user_content = "\n".join(
        [
            f"RFP: {rfp.title}",
            f"Client: {rfp.client} | Sector: {rfp.sector} | Location: {rfp.location or '(n/a)'}",
            f"Estimated value (metadata): {rfp.estimated_value or '(not set)'}",
            f"\n=== Stage 1 Go/No-Go ===\n{stage_one[:12_000]}",
            f"\n=== Stage 2 Structural map (deliverables) ===\n{stage_two[:10_000]}",
            f"\n=== 00_Guide_Pricing (KB) ===\n{guide_text}",
            f"\n=== RFP excerpt ===\n{rfp_context[:28_000]}",
        ]
    )

    messages = [
        {"role": "system", "content": STAGE3_BUDGET_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        raw, provider = await llm.chat_json(
            messages,
            max_tokens=8192,
            temperature=0.2,
        )
    except LlmError as exc:
        logger.warning(
            "Stage 3 budget first pass failed (%s), retrying with compact output",
            exc,
        )
        compact_user = (
            user_content
            + "\n\nIMPORTANT: Return COMPACT JSON only. Maximum 20 lineItems. "
            "Keep rfpBudgetNotes under 500 characters. No markdown or commentary. "
            "agencyRevenueEstimate MUST be > 0 for commission RFPs (rate × pass-through). Never return $0 agency revenue."
        )
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": STAGE3_BUDGET_PROMPT},
                {"role": "user", "content": compact_user},
            ],
            max_tokens=6144,
            temperature=0.2,
        )

    now = datetime.now(timezone.utc).isoformat()
    flags = [
        str(f)
        for f in (raw.get("pricingFlags") or [])
        if str(f).strip() and not str(f).strip().upper().startswith("[VERIFY:")
    ]
    if not stage_one_ready:
        flags.append(
            "[PRICING FLAG: Stage 1 Go/No-Go not complete — run fit analysis for tier selection]"
        )
    if not stage_two_ready:
        flags.append(
            "[PRICING FLAG: Stage 2 structural map missing — run full proposal or Sections 1–3 first]"
        )
    if guide_text.startswith("(No 00_Guide_Pricing"):
        flags.append(
            "[PRICING FLAG: No 00_Guide_Pricing in KB — ingest pricing guide before submission]"
        )

    line_items = _parse_line_items(raw.get("lineItems"))
    if not line_items:
        logger.warning(
            "Stage 3 budget returned no lineItems (keys=%s)",
            sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
        )

    confidence = int(raw.get("confidence") or 0)
    if not stage_one_ready or not stage_two_ready:
        confidence = min(confidence, 50)
    if guide_text.startswith("(No 00_Guide_Pricing"):
        confidence = min(confidence, 40)

    extras = parse_budget_extras(raw)
    budget = ProposalBudget(
        rfpId=rfp_id,
        rfpBudgetCap=_parse_budget_cap(raw.get("rfpBudgetCap")),
        rfpBudgetNotes=str(raw.get("rfpBudgetNotes") or "")[:4000],
        feeStructure=str(raw.get("feeStructure") or ""),
        pricingTier=str(raw.get("pricingTier") or "Average"),
        budgetFormat=str(raw.get("budgetFormat") or "phased"),
        formHourlyRate=(
            float(raw["formHourlyRate"])
            if isinstance(raw.get("formHourlyRate"), (int, float))
            else None
        ),
        formMonthlyRate=(
            float(raw["formMonthlyRate"])
            if isinstance(raw.get("formMonthlyRate"), (int, float))
            else None
        ),
        formAnnualRate=(
            float(raw["formAnnualRate"])
            if isinstance(raw.get("formAnnualRate"), (int, float))
            else None
        ),
        formRateNotes=str(raw.get("formRateNotes") or "")[:2000],
        lineItems=line_items,
        tiers=_parse_tiers(raw.get("tiers")),
        recommendedTierId=raw.get("recommendedTierId"),
        agencyRevenueEstimate=(
            float(raw["agencyRevenueEstimate"])
            if isinstance(raw.get("agencyRevenueEstimate"), (int, float))
            else None
        ),
        lineItemSum=extras.get("line_item_sum"),
        agencyFeeSubtotal=extras.get("agency_fee_subtotal"),
        clientMediaPassthrough=extras.get("client_media_passthrough"),
        totalClientInvoicing=extras.get("total_client_invoicing"),
        commissionRate=extras.get("commission_rate"),
        lumpSumTotal=extras.get("lump_sum_total"),
        directExpensesTotal=extras.get("direct_expenses_total"),
        commissionModel=raw.get("commissionModel"),
        pricingFlags=flags,
        qualifyingLanguage=_normalize_qualifying_language(raw.get("qualifyingLanguage")),
        scopeAdjustments=[
            str(s) for s in (raw.get("scopeAdjustments") or []) if str(s).strip()
        ],
        scopeSummary=str(raw.get("scopeSummary") or ""),
        designBrief=str(raw.get("designBrief") or ""),
        optionTermNotes=str(raw.get("optionTermNotes") or ""),
        mediaSpendNotes=str(raw.get("mediaSpendNotes") or ""),
        verifiedRates=_parse_verified_rates(raw.get("verifiedRates")),
        kbSources=kb_sources[:20],
        kbBucketsUsed=["pricing", "reference"] if kb_sources else [],
        confidence=confidence,
        updatedAt=now,
        provider=provider,
    )

    budget = run_budget_editor_pass(
        budget,
        rfp_sections=prior_research.rfp_sections if prior_research else [],
        rfp_context=rfp_context,
    )

    revenue = float(budget.agency_revenue_estimate or 0)
    if revenue <= 0 and (
        budget.commission_rate
        or budget.client_media_passthrough
        or budget.commission_model
    ):
        flags = list(budget.pricing_flags)
        flags.append(
            "[PRICING FLAG: agencyRevenueEstimate is $0 but commission model applies — "
            "set commissionRate × clientMediaPassthrough or agency_fee line items before submission]"
        )
        budget = budget.model_copy(update={"pricing_flags": flags})

    stage_one_text, _ = _stage_one_text(rfp)
    fee_memo = await generate_fee_justification_memo(
        rfp=rfp,
        budget=budget,
        stage_one_excerpt=stage_one_text,
    )
    if fee_memo:
        budget = budget.model_copy(update={"fee_justification_memo": fee_memo})

    if prior_research:
        research = prior_research.model_copy(
            update={"budget": budget, "updated_at": now, "provider": provider}
        )
    else:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            budget=budget,
            updatedAt=now,
            provider=provider,
        )
    save_research_cache(research)

    logger.info(
        "Stage 3 budget for %s: %d line items, tier=%s, format=%s, confidence=%d",
        rfp_id,
        len(line_items),
        budget.pricing_tier,
        budget.budget_format,
        budget.confidence,
    )
    return budget, research


def reconcile_cached_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Re-run deterministic budget editor on cached budget (no LLM regen)."""
    _rfp, _content, rfp_context = load_rfp_for_proposal(rfp_id)
    research = get_research_cache(rfp_id)
    if not research or not research.budget:
        raise ProposalError(
            "No cached budget to reconcile. Run Phase 3.5 budget generation first.",
            status_code=400,
        )

    budget = run_budget_editor_pass(
        research.budget,
        rfp_sections=research.rfp_sections,
        rfp_context=rfp_context,
    )
    now = datetime.now(timezone.utc).isoformat()
    research = research.model_copy(update={"budget": budget, "updated_at": now})
    save_research_cache(research)
    logger.info(
        "Budget reconciled for %s: revenue=%s, lump=%s, %d line items",
        rfp_id,
        budget.agency_revenue_estimate,
        budget.lump_sum_total,
        len(budget.line_items),
    )
    return budget, research
