"""Stage 3 budget: 00_Guide_Pricing + Stage 1/2 context + RFP excerpt."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import (
    BudgetLineItem,
    BudgetLineGrounding,
    PricingTier,
    PricingAuditFlag,
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
from app.services.proposal_budget_playbook import BUDGET_PLAYBOOK_CANONICAL
from app.services.proposal_repository import aget_research_cache, asave_research_cache

logger = logging.getLogger(__name__)

GUIDE_SEARCH_CHAR_LIMIT = 24_000
# Full pinned guide is ~34k; do not truncate below that or PM lines (9.x) are lost.
PINNED_GUIDE_CHAR_LIMIT = 80_000
# Canonical pricing guide — always pin by filename for rate-card builds.
PRICING_GUIDE_FILE_NAMES: tuple[str, ...] = (
    "00_Guide_Pricing.docx",
    "00_Guide_Pricing.pdf",
    "00_Guide_Pricing.md",
)
# When the guide is present but extraction yields essentially nothing usable,
# fail closed instead of shipping an all-manual budget from a junk rate card.
_MIN_USABLE_RATE_CARD_RATES = 2


def assert_rate_card_usable(
    *,
    rate_card: Any,
    guide_text: str,
    guide_missing: bool | None = None,
) -> None:
    """Raise when pricing guide text exists but the rate card is not bindable."""
    missing = (
        guide_missing
        if guide_missing is not None
        else bool((guide_text or "").startswith("(No 00_Guide_Pricing"))
    )
    if missing:
        return
    rates = list(getattr(rate_card, "rates", None) or [])
    if len(rates) < _MIN_USABLE_RATE_CARD_RATES:
        logger.error(
            "pricing_rate_card_unusable rates=%s guide_chars=%s",
            len(rates),
            len(guide_text or ""),
        )
        raise ProposalError(
            "Pricing guide rate card unusable: extracted too few bindable rates "
            f"({len(rates)}). Re-fetch 00_Guide_Pricing before building budget.",
            status_code=422,
        )


STAGE3_BUDGET_PROMPT = """You are zö agency's Stage 3 Budget assistant. Build a complete, defensible budget using ONLY:
- The Pricing Guide menu below (tier ranges)
- 00_Guide_Pricing excerpts from the knowledge base
- Stage 1 Go/No-Go analysis, Stage 2 structural map, and RFP excerpt
- 06_WON / 07_FIN proposal excerpts ONLY for budget page FORMAT examples — never for
  inventing named-person hourly rates (individual ZO member $/hr are NOT in the KB)

Do not invent numbers. Every amount must trace to a Pricing Guide line item at the selected tier or an explicit KB excerpt.
Never reverse-engineer a line item's rate or extended amount so the sum hits a target — change tier or scope instead.
One-time deliverables (setup, development, package) must not be multiplied by 12 to simulate recurring fees.

""" + BUDGET_PLAYBOOK_CANONICAL + """

=== PROCESS (follow in order) ===

PHASE 1 — Extract five signals from the RFP and prior stages (align with playbook §1–2):
1. Budget ceiling (hard cap if stated — stay under it)
2. Cost weight in scoring → tier: 25%+ = Low, 15–20% = Average (default), 10% or less = High — state tier + rationale
3. Budget format: phased | personnel_loading | service_menu | blended_rate_form (match RFP, not habit)
4. Deliverables from Stage 2 → each becomes a line item (one-time vs recurring per playbook §3)
5. Travel — add direct expenses line if zö is out of region

PHASE 2 — Pick ONE pricing tier (Low / Average / High) for the entire proposal.

PHASE 3 — Map every RFP deliverable to a Pricing Guide line item as a PHASE / DELIVERABLE row:
- description MUST name the phase + deliverable (e.g. "Phase 1 Discovery — Stakeholder interviews
  (RFP §6.2 Item 1)"). NEVER use bare "Strategy Lead — Name" as the only description.
- category = phase name (Discovery / Strategy / Tactical Plan / Roadmap / etc.)
- namedPerson / roleTitle are optional staffing notes — not a substitute for the deliverable label.
- Prefer budgetFormat=phased (or service_menu) UNLESS THIS RFP explicitly requires:
  (a) personnel_loading — hourly rates BY ROLE / labor category (often with Year-2 / Year-3 % increases), OR
  (b) blended_rate_form — a single hourly / monthly / annual block.
- When THIS RFP asks for a role-by-role hourly table, budgetFormat MUST be personnel_loading.
  Emit one agency_fee lineItem per RFP-named role with unit=hours (quantity may be 1 for rate display),
  rate = guide labor-category hourly, roleTitle = exact RFP role label. Put Year-2 / Year-3 % in
  optionTermNotes (e.g. "Year-2 increase: 3%. Year-3 increase: 3%."). Do NOT substitute a
  fixed-fee / monthly retainer phase table for that instrument — evaluators score the hourly table.
- budgetFormat is AUTHORITATIVE for the manuscript Cost section. Downstream renderers will not
  second-guess it with keyword scans — choose the format that matches THIS RFP's scored instrument.
- One guide line → one lineItem. rateSource cites the menu id + tier (e.g. "1.1 — Average").

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

TRAVEL / DIRECT EXPENSES (no double-billing):
- Put travel EITHER as one lineItem (category Travel) OR in directExpensesTotal — NEVER both for the
  same trips/amount. If Reimbursable Expenses prose cites one $X travel estimate, the table must
  show that $X only once.

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
- NEVER invent agencyRevenueEstimate / commission dollars when media spend is unknown.
- When LOCKED PricingContract.mediaSpendAnnual is null and feeModel is commission/hybrid:
  retain commission shape with MANUAL FILL placeholders; agencyRevenueEstimate MAY be null —
  do NOT invent media base or commission fee amounts.
- When mediaSpendAnnual is set (or non-commission agency fees apply): NEVER return
  agencyRevenueEstimate = 0 when fee income applies — use rate × pass-through or agency_fee rows.
- NEVER show "$0" for "Agency revenue estimate" when evidenced commission math applies —
  the commission dollar amount IS the agency revenue (rate × pass-through or sum of agency_fee rows).
- lineItemSum may be large (mostly pass-through media); agencyRevenueEstimate is still the fee income only.
- lumpSumTotal and optionTermNotes MUST cite the same positive annual agency fee as agencyRevenueEstimate
  when that fee is evidenced — otherwise leave MANUAL FILL / pricing flags.
- If estimated annual media spend is in RFP/Stage 1, use it for clientMediaPassthrough and compute commission.

STAFF HOURS (when RFP Section D requires hours and billing rates):
- Add a "## Staff Hours" table: Labor category / classification | Task/Scope line | Hours | Rate | Extended
- Use WORK / labor-category hourly rates from 00_Guide_Pricing rate card only.
- NEVER invent individual ZO team-member hourly rates (Sonja, Curt, Justin, etc. $/hr are NOT in KB).
- namedPerson may appear as a staffing note only — the rate MUST cite a guide labor category / menu id, not a person.
- If the RFP demands named-person loaded rates and KB has none: leave rate as
  [PRICING FLAG: Sonja approve loaded rate — {role}] or [VERIFY: named person hourly rate — not in KB]
  — do NOT fabricate verifiedRates.hourlyRate for a personName.
- Commission-model RFPs STILL need this transparency table — commission is total compensation but evaluators require hours

NAMED-PERSON RATES BAN:
- verifiedRates should be EMPTY unless a source string cites 00_Guide_Pricing labor-category text verbatim.
- Do not pull burdened person rates from 07_FIN prior proposals.

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
When uncertain of a rate or guide match: set isManualFill=true on the lineItem, leave sourceRateId null, and add a pricing flag — NEVER invent a dollar amount to look confident.

PHASE 4 — Stress-test before finalizing:
- Total sustains 50% wages / 30% G&A / 20% profit
- Under RFP ceiling (scope down if over — do not price below floor)
- Leave 15–20% room for scope expansion
- PM is 5–8% of total

PHASE 4b — PHASE / LINE CONSISTENCY (submission quality — do NOT skip):
1. NO DOUBLE-COUNTING: Each guide line / dollar amount appears in EXACTLY one lineItem.
   Never "carry" the same $X into Phase A totals AND list it again as Phase B's entire fee.
   If a deliverable is bundled inside another phase's fee, say so in notes and give Phase B
   its OWN guide-backed fee for remaining work — or omit Phase B as a priced phase.
2. PHASE FEE vs NARRATIVE DEPTH: If Technical Ability / approach narrative describes a phase
   with substantial deliverables (digital, social, content, PR, advertising, martech/CRM, etc.),
   that phase's fee MUST fund that work with real guide lines — not a token "$1,000 packaging"
   fee. Underpricing a phase that the manuscript sells at length is a submission risk.
3. FULL PHASE COVERAGE: If the approach names N delivery phases (e.g. Discovery, Strategy,
   Tactical Plan, Roadmap & Handoff), lineItems MUST price ALL of them with guide-backed fees.
   Never price only Discovery/Strategy while the narrative promises Tactical + Roadmap.
   Roadmap → Category 10.5; tactical channels → Categories 04–07; PM → Category 09.
4. HONEST TIER LANGUAGE: If any line is discounted below the selected tier band (e.g. Average
   $12k–$18k taken at $10k), state that clearly in the line notes AND qualifyingLanguage
   ("scoped/discounted below Average band for …") — never claim the whole quote sits cleanly
   inside Average while a line sits below the band.
5. BOTTOM-LINE TOTAL REQUIRED: Always produce lumpSumTotal / agencyRevenueEstimate as a single
   submission-ready project total when the RFP asks for detailed pricing. If a required phase
   has no guide line, either (a) map the closest guide bundle (e.g. 10.5 Implementation Roadmap)
   and price it, OR (b) add pricingFlags requiring Sonja's pick AND still price a provisional
   total with [PRICING FLAG: Roadmap provisional — Sonja confirm] — never leave "no final total"
   as the only state when Section 8 / cost proposal requires pricing.
   Professional fees and travel/reimbursables must be separable — never label travel as
   "professional fees."
6. CREDIBLE FLOOR: For a multi-phase institutional marketing plan, professional fees (excluding
   travel) must not collapse to a few thousand dollars. Scope deliverables honestly under RFP
   thresholds — never invent a percentage that shrinks guide rates ~100×.
7. HARD CAP HYGIENE: rfpBudgetCap is ONLY an RFP maximum compensation / NTE / published ceiling.
   NEVER set it from travel estimates, PM rows, roadmap rows, or notes that say "budget envelope"
   colloquially. Yearly Annual Allocation rows are envelopes — not fee lines.
   NEVER set rfpBudgetCap equal to your own proposed total to make the bid "fit" a ceiling.
   Program/media "allocating up to $X for advertising" is NOT rfpBudgetCap — that is a separate
   program/media envelope enforced deterministically after this JSON.
8. Traceability: each phase fee notes which RFP section/items it covers — without reusing the
   same extended dollars across phases.

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

PHASE 6 — Client-facing copy (MUST be short and clear for the buyer):
- scopeSummary: 2–4 short sentences max. MUST state the SAME total as lumpSumTotal / sum(lineItems)
  (+ directExpensesTotal if used). Never cite a second conflicting dollar total.
- No internal jargon (no "guide line", "Sonja", "00_Guide_Pricing", "agency revenue estimate",
  "double-count").
- qualifyingLanguage: four SHORT markdown blocks — ### Investment Framing, ### Scope Protection,
  ### Reimbursable Expenses, ### Revision Rounds. Each block: 1–2 bullets OR a mix table
  (| Component | Share | Amount | Notes |). NEVER one wall paragraph of percentages and dollars.
  Plain English a procurement officer can skim in under a minute.
- rfpBudgetNotes: optional one short paragraph OR empty — never a multi-page methodology essay.
- lineItem descriptions: phase + deliverable tied to RFP items. Put guide citations in rateSource only.
- Do NOT write long "build-out" prose that re-explains every math step in the section narrative.
- optionTermNotes: client language only ("proposed fees") — never "agency revenue estimate".

PHASE 6b — qualifyingLanguage MUST include all four blocks as markdown headings + bullets/tables
(not one paragraph): Investment Framing, Scope Protection, Reimbursable Expenses, Revision Rounds.
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

CRITICAL — HARD CAP / YEAR ALLOCATIONS (submission disqualifier if wrong):
- If the RFP states a maximum compensation / NTE / total proposed price ceiling (e.g. $2,950,000),
  set rfpBudgetCap to that number and keep agencyRevenueEstimate + lumpSumTotal AT OR UNDER it.
- Yearly "Annual Allocation Year 1/2/3" (or similar) figures are the BUDGET ENVELOPE, not cost lines.
  NEVER add them as lineItems — that double-counts and exceeds the hard cap.
- lineItems = billable work only (Discovery, Strategy, Content, Digital, PM, etc.) that SUM to ≤ rfpBudgetCap.
- If scope would exceed the cap, scope DOWN (fewer hours/deliverables) — do not invent extra rows to pad.

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


async def fetch_pricing_guide_context(
    rfp: RfpRecord,
    *,
    stage_two: str = "",
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """Retrieve 00_Guide_Pricing from Supermemory (Stage 3, chat explain, section edits)."""
    return await _fetch_guide_context(rfp, stage_two, focus_hint=focus_hint)


async def _fetch_pinned_pricing_guide() -> tuple[str, list[str]] | None:
    """Load the canonical pricing guide by exact filename (full indexed text).

    Prefer this over fuzzy search: hybrid hits are summaries, documents hits are
    mid-table chunks, and query thresholds can return 0 hits even when the doc exists.
    """
    if not supermemory.is_configured():
        return None
    last_error: Exception | None = None
    for file_name in PRICING_GUIDE_FILE_NAMES:
        try:
            document = await supermemory.find_document_by_file_name(file_name)
            if not document:
                continue
            custom_id = supermemory.document_fetch_key(document)
            if not custom_id:
                logger.warning(
                    "pricing_guide_pin_missing_fetch_key file_name=%s",
                    file_name,
                )
                continue
            content = await supermemory.get_document_content(custom_id=custom_id)
            if not (content or "").strip():
                logger.warning(
                    "pricing_guide_pin_empty_content file_name=%s custom_id=%s",
                    file_name,
                    custom_id,
                )
                continue
            text = content.strip()
            if len(text) > PINNED_GUIDE_CHAR_LIMIT:
                text = text[:PINNED_GUIDE_CHAR_LIMIT]
            logger.info(
                "pricing_guide_pinned file_name=%s chars=%s",
                file_name,
                len(text),
            )
            return text, [file_name]
        except supermemory.SupermemoryError as exc:
            last_error = exc
            logger.warning(
                "pricing_guide_pin_failed file_name=%s error=%s",
                file_name,
                exc,
            )
    if last_error:
        logger.warning("pricing_guide_pin_exhausted last_error=%s", last_error)
    return None


async def _fetch_guide_context(
    rfp: RfpRecord,
    stage_two: str,
    *,
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """Retrieve 00_Guide_Pricing: pin by filename first, search only as fallback."""
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    pinned = await _fetch_pinned_pricing_guide()
    if pinned is not None:
        return pinned

    logger.warning(
        "pricing_guide_pin_miss — falling back to search rfp_id=%s",
        rfp.id,
    )
    scope_hint = stage_two[:200] if stage_two else (rfp.sector or "")
    hint = (focus_hint or "")[:300]
    from app.services.proposal_knowledge_base_tools import sanitize_pricing_guide_query

    queries = [
        "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management contingency qualifying language",
        "00_Guide_Pricing 4.4 Email Newsletter Design Setup one-time average tier",
        "00_Guide_Pricing 9.1 9.2 Project Management short projects campaign-specific 5-8 percent floor",
        sanitize_pricing_guide_query(
            f"00_Guide_Pricing {scope_hint[:120]}",
            rfp_client=rfp.client or "",
            rfp_title=rfp.title or "",
        ),
    ]
    if hint:
        queries.insert(
            1,
            sanitize_pricing_guide_query(
                f"00_Guide_Pricing {hint[:200]}",
                rfp_client=rfp.client or "",
                rfp_title=rfp.title or "",
            ),
        )
    chunks: list[str] = []
    sources: list[str] = []
    seen_chunk_keys: set[str] = set()
    for query in queries:
        # Prefer pricing category; reference only as secondary fallback.
        for category in ("pricing", "reference"):
            text, srcs = await search_knowledge_base(
                query,
                limit=8,
                category=category,
                max_chars=GUIDE_SEARCH_CHAR_LIMIT // 3,
            )
            if text and not text.startswith("("):
                key = text[:200]
                if key not in seen_chunk_keys:
                    seen_chunk_keys.add(key)
                    chunks.append(text)
            for src in srcs:
                if src not in sources:
                    sources.append(src)

    combined = "\n\n---\n\n".join(chunks)[:GUIDE_SEARCH_CHAR_LIMIT]
    return combined or "(No 00_Guide_Pricing content in KB — ingest pricing guide.)", sources


_NESTED_LINE_ITEM_KEYS = (
    "lineItems",
    "line_items",
    "lineitems",
    "budgetLineItems",
    "items",
    "rows",
    "phases",
    "budget",
    "deliverables",
)

_VALID_LINE_ITEM_TYPES = {"agency_fee", "client_passthrough", "direct_expense"}


def _looks_like_line_item_dict(item: dict[str, Any]) -> bool:
    keys = set(item)
    if keys & {"description", "roleTitle", "role", "name", "extended", "rate", "hourlyRate"}:
        if not (keys & set(_NESTED_LINE_ITEM_KEYS)):
            return True
    return False


def _collect_line_item_dicts(payload: Any, out: list[dict[str, Any]], *, depth: int = 0) -> None:
    if depth > 6 or payload is None:
        return
    if isinstance(payload, str) and payload.strip().startswith(("{", "[")):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return
    if isinstance(payload, list):
        for row in payload:
            _collect_line_item_dicts(row, out, depth=depth + 1)
        return
    if not isinstance(payload, dict):
        return
    if _looks_like_line_item_dict(payload):
        out.append(payload)
        return
    for key in _NESTED_LINE_ITEM_KEYS:
        if key in payload:
            _collect_line_item_dicts(payload.get(key), out, depth=depth + 1)


def _parse_line_items(raw_items: Any) -> list[BudgetLineItem]:
    collected: list[dict[str, Any]] = []
    _collect_line_item_dicts(raw_items, collected)
    items: list[BudgetLineItem] = []
    for index, item in enumerate(collected):
        description = (
            item.get("description")
            or item.get("roleTitle")
            or item.get("role")
            or item.get("name")
            or "Budget line item"
        )
        line_type = item.get("lineItemType") or item.get("line_item_type")
        if str(line_type or "") not in _VALID_LINE_ITEM_TYPES:
            line_type = "agency_fee"
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
                        "lineItemType": line_type,
                    }
                )
            )
        except Exception:
            logger.debug("Skipped unparseable line item", exc_info=True)
    return items


def _line_items_payload_from_raw(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return None
    for key in ("lineItems", "line_items", "lineitems", "budgetLineItems"):
        payload = raw.get(key)
        if payload not in (None, "", [], {}):
            return payload
    for key in ("phases", "budget", "items", "rows", "deliverables"):
        payload = raw.get(key)
        if payload not in (None, "", [], {}):
            return payload
    return None


def _parse_line_items_from_raw(raw: Any) -> list[BudgetLineItem]:
    return _parse_line_items(_line_items_payload_from_raw(raw) if isinstance(raw, dict) else raw)


_FALLBACK_MENU_HINTS = ("1.1", "2.1", "3.1", "5.3", "9.1")
_FALLBACK_LABELS = (
    "Discovery",
    "Strategy",
    "Creative",
    "Digital",
    "Project Management",
)


def _personnel_loading_missing_bindable_hourly(
    budget: ProposalBudget,
    rate_card: Any | None,
) -> bool:
    """True when personnel_loading has role rows but no guide hourly rates to bind."""
    if (budget.budget_format or "").casefold() != "personnel_loading":
        return False
    from app.services.pricing_rate_card_builder import bindable_rates

    hourly_card = [
        r for r in bindable_rates(rate_card or None) if getattr(r, "unit", "") == "hour"
    ]
    if hourly_card:
        return False
    labor_lines = [
        item
        for item in budget.line_items
        if (item.role_title or "").strip()
        or (item.unit or "").casefold() in {"hour", "hours", "hr", "hrs"}
    ]
    if not labor_lines:
        return False
    return all(
        (item.rate is None or float(item.rate or 0) <= 0)
        and (item.extended is None or float(item.extended or 0) <= 0)
        for item in labor_lines
    )


def coerce_budget_to_phased_from_guide(
    budget: ProposalBudget,
    rate_card: Any | None,
    *,
    rfp_text: str = "",
    reason: str = "",
) -> tuple[ProposalBudget, list[str]]:
    """Rebuild as phased fixed fees when hourly personnel_loading cannot be grounded."""
    from app.models.pricing_rate_card import PricingRateCard
    from app.services.proposal_budget_content import _professional_zo_budget_terms
    from app.services.proposal_budget_format_judge import rfp_indicates_fixed_pricing_table
    from app.services.proposal_budget_validation import infer_line_item_type

    logs: list[str] = []
    fmt = (budget.budget_format or "phased").casefold()
    labor_stubs = [
        item
        for item in budget.line_items
        if (item.role_title or "").strip()
        or (item.unit or "").casefold() in {"hour", "hours", "hr", "hrs"}
    ]
    all_labor_unpriced = bool(labor_stubs) and all(
        (item.rate is None or float(item.rate or 0) <= 0)
        and (item.extended is None or float(item.extended or 0) <= 0)
        for item in labor_stubs
    )
    needs_phased = all_labor_unpriced and (
        fmt == "personnel_loading"
        or rfp_indicates_fixed_pricing_table(rfp_text)
        or _personnel_loading_missing_bindable_hourly(budget, rate_card)
    )
    if not needs_phased:
        return budget, logs

    card = rate_card if isinstance(rate_card, PricingRateCard) else None
    skeleton = skeleton_line_items_from_rate_card(card)
    if not skeleton:
        logs.append("Budget coerce skipped — no guide-backed skeleton line items.")
        return budget, logs

    direct_lines = [
        item
        for item in budget.line_items
        if infer_line_item_type(item) in {"direct_expense", "client_passthrough"}
        and float(item.extended or item.rate or 0) > 0
    ]

    flags = list(budget.pricing_flags or [])
    detail = reason or (
        "RFP fixed Pricing Table / no hourly rates in 00_Guide_Pricing"
    )
    flag = (
        "[PRICING FLAG: Cost instrument rebuilt as phased fixed fees from "
        f"00_Guide_Pricing — {detail}.]"
    )
    if flag not in flags:
        flags.append(flag)

    scope = (
        f"zö agency proposes transparent, fixed project fees structured for this "
        f"engagement. Complete the official Pricing Table attachment per RFP Section VI."
    )

    qualifying = _professional_zo_budget_terms()

    logs.append(
        "Budget: coerced personnel_loading → phased fixed fees from 00_Guide_Pricing."
    )
    return budget.model_copy(
        update={
            "budget_format": "phased",
            "line_items": [*skeleton, *direct_lines],
            "pricing_flags": flags,
            "scope_summary": scope[:2000],
            "qualifying_language": qualifying[:2000],
            "option_term_notes": "",
            "fee_structure": "fixed phased fees",
        }
    ), logs


def skeleton_line_items_from_rate_card(rate_card: Any) -> list[BudgetLineItem]:
    """Guide-backed rows when the LLM omits lineItems — never invent dollar amounts."""
    from app.services.pricing_rate_card_builder import bindable_rates

    try:
        bindable = list(bindable_rates(rate_card) if rate_card is not None else [])
    except Exception:
        bindable = list(getattr(rate_card, "rates", None) or [])
    avg = [
        r
        for r in bindable
        if str(getattr(r, "tier", "") or "").lower() in {"average", "avg", ""}
    ]
    pool = avg or bindable
    picked: list[Any] = []
    seen: set[str] = set()
    for hint in _FALLBACK_MENU_HINTS:
        for rate in pool:
            rid = str(getattr(rate, "rate_id", "") or "")
            if getattr(rate, "menu_id", "") == hint and rid not in seen:
                picked.append(rate)
                seen.add(rid)
                break
    for rate in pool:
        if len(picked) >= 5:
            break
        rid = str(getattr(rate, "rate_id", "") or "")
        amount = getattr(rate, "amount", None)
        if rid in seen or amount is None:
            continue
        picked.append(rate)
        seen.add(rid)

    items: list[BudgetLineItem] = []
    for index, rate in enumerate(picked, start=1):
        amount = getattr(rate, "amount", None)
        if amount is None:
            amount = getattr(rate, "amount_low", None)
        if amount is None:
            continue
        unit_raw = str(getattr(rate, "unit", "fixed") or "fixed")
        unit = "flat" if unit_raw in {"fixed", "unknown"} else unit_raw
        menu = str(getattr(rate, "menu_id", "") or "").strip()
        service = str(getattr(rate, "service", "") or "Guide service").strip()
        items.append(
            BudgetLineItem.model_validate(
                {
                    "id": f"li-fallback-{index}",
                    "category": "labor",
                    "description": f"{menu} {service}".strip(),
                    "unit": unit if unit in {"flat", "hours", "monthly", "annual", "percent"} else "flat",
                    "quantity": 1,
                    "rate": float(amount),
                    "extended": float(amount),
                    "lineItemType": "agency_fee",
                    "sourceRateId": getattr(rate, "rate_id", None),
                    "rateSource": getattr(rate, "source_doc", None) or "00_Guide_Pricing",
                    "notes": (
                        "Skeleton from 00_Guide_Pricing — model omitted lineItems. "
                        "Refine in Budget before submit."
                    ),
                }
            )
        )
    if items:
        return items
    for index, label in enumerate(_FALLBACK_LABELS, start=1):
        items.append(
            BudgetLineItem.model_validate(
                {
                    "id": f"li-fallback-{index}",
                    "category": "labor",
                    "description": label,
                    "unit": "flat",
                    "quantity": 1,
                    "lineItemType": "agency_fee",
                    "isManualFill": True,
                    "notes": (
                        "[MANUAL FILL: Sonja — set fee from 00_Guide_Pricing; "
                        "model omitted line items]"
                    ),
                }
            )
        )
    return items


_LINE_ITEMS_RETRY_USER = (
    "Your previous JSON omitted a usable lineItems array (required for submission). "
    "Return the COMPLETE budget JSON again. lineItems MUST be a non-empty array with "
    "at least 5 rows covering Discovery, Strategy, Creative, Digital/PM, and Travel when "
    "applicable. Each item needs id, description, category, lineItemType, quantity, rate, "
    "extended (numbers). agencyRevenueEstimate and lumpSumTotal MUST match summed agency fees."
)


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
    """Keep only rates with an explicit guide source — never invent named-person $/hr."""
    if not isinstance(raw_rates, list):
        return []
    rates: list[VerifiedRate] = []
    for item in raw_rates:
        if not isinstance(item, dict):
            continue
        try:
            rate = VerifiedRate.model_validate(item)
        except Exception:
            continue
        source = (rate.source or "").casefold()
        # Individual person rates are not in KB — drop unless clearly guide-grounded.
        if rate.person_name and rate.hourly_rate is not None:
            guide_grounded = any(
                token in source
                for token in (
                    "00_guide_pricing",
                    "guide_pricing",
                    "labor category",
                    "rate card",
                    "menu",
                )
            )
            if not guide_grounded:
                logger.info(
                    "Dropping unverified named-person rate for %s (source=%r)",
                    rate.person_name,
                    rate.source,
                )
                continue
        rates.append(rate)
    return rates


def _parse_budget_cap(raw_cap: Any) -> float | None:
    if isinstance(raw_cap, (int, float)) and float(raw_cap) > 0:
        return float(raw_cap)
    return None


def _manuscript_pricing_digest(draft: Any) -> str:
    """Pull approach + current budget prose so Stage 3 prices fund what narrative promises."""
    from app.models.proposal import ProposalDraft

    if not isinstance(draft, ProposalDraft):
        return ""
    parts: list[str] = []
    for section in draft.sections:
        title = (section.title or "").casefold()
        content = (section.content or "").strip()
        if not content:
            continue
        is_budget = any(
            k in title
            for k in ("budget", "pricing", "fee", "cost proposal", "compensation")
        )
        is_approach = any(
            k in title
            for k in (
                "technical",
                "ability",
                "approach",
                "methodology",
                "work plan",
                "scope",
                "phase",
            )
        )
        if not (is_budget or is_approach):
            continue
        label = "BUDGET SECTION" if is_budget else "APPROACH / PHASE NARRATIVE"
        parts.append(f"### {label}: {section.title}\n{content[:4500]}")
        if len(parts) >= 4:
            break
    return "\n\n".join(parts)


STAGE3A_GROUNDING_PROMPT = """You are Stage 3.5a budget grounding auditor.
Audit a proposed budget against the RFP + pricing guide.

Return JSON only:
{
  "lineItemGrounding":[
    {
      "lineItemId":"...",
      "deliverable":"...",
      "guideSku":"...",
      "rfpRequirement":"...",
      "tierChosen":"Low|Average|High",
      "tierRationale":"...",
      "amount":1234.56,
      "fieldType":"agency_fee|client_passthrough|direct_expense",
      "derivation":"exact guide/range or formula explanation",
      "grounded":true,
      "note":""
    }
  ],
  "pricingAuditFlags":[
    {"severity":"low|medium|high|blocker","concern":"...","lineItemId":"...","note":"..."}
  ]
}

Rules:
- If a line item lacks a concrete guide SKU/range mapping, mark grounded=false and emit a high/blocker flag.
- Flag plausible scope overlap/double-charging.
- Validate media disclosure integrity: pass-through and agency commission must be separable.
- Flag PM ratio concerns versus expected 5-8% unless explicit justification exists.
"""


async def _run_budget_grounding_audit(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_context: str,
    stage_two: str,
    guide_text: str,
    budget: ProposalBudget,
) -> tuple[list[BudgetLineGrounding], list[PricingAuditFlag]]:
    rows = [
        {
            "lineItemId": item.id,
            "category": item.category,
            "description": item.description,
            "quantity": item.quantity,
            "unit": item.unit,
            "rate": item.rate,
            "extended": item.extended,
            "rateSource": item.rate_source,
            "lineItemType": item.line_item_type,
        }
        for item in (budget.line_items or [])
    ]
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": STAGE3A_GROUNDING_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp_title}\nClient: {rfp_client}\n\n"
                        f"Pricing tier: {budget.pricing_tier}\n\n"
                        f"=== Stage 2 map ===\n{stage_two[:9000]}\n\n"
                        f"=== Guide excerpts ===\n{guide_text[:16000]}\n\n"
                        f"=== RFP excerpt ===\n{rfp_context[:13000]}\n\n"
                        f"=== Budget line items ===\n{rows}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.0,
            node_name="stage35a_budget_grounding",
        )
    except LlmError as exc:
        logger.warning("Stage 3.5a grounding audit failed: %s", exc)
        return [], []

    grounding: list[BudgetLineGrounding] = []
    for row in (raw.get("lineItemGrounding") or []):
        if not isinstance(row, dict):
            continue
        if not str(row.get("lineItemId") or "").strip():
            continue
        try:
            grounding.append(BudgetLineGrounding(**row))
        except Exception:
            continue

    flags: list[PricingAuditFlag] = []
    for row in (raw.get("pricingAuditFlags") or []):
        if not isinstance(row, dict):
            continue
        concern = str(row.get("concern") or "").strip()
        if not concern:
            continue
        try:
            flags.append(PricingAuditFlag(**row))
        except Exception:
            sev = str(row.get("severity") or "medium").strip().lower()
            if sev not in {"low", "medium", "high", "blocker"}:
                sev = "medium"
            flags.append(
                PricingAuditFlag(
                    severity=sev,
                    concern=concern,
                    lineItemId=str(row.get("lineItemId") or "").strip() or None,
                    note=str(row.get("note") or ""),
                )
            )
    return grounding, flags


async def generate_proposal_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Stage 3 budget: Stage 1 + Stage 2 + 00_Guide_Pricing + RFP excerpt → single LLM pass."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    from app.core.step_debug_logger import pipeline_step, step_trace, summarize_budget

    rfp, _content, rfp_context = load_rfp_for_proposal(rfp_id)
    prior_research = await aget_research_cache(rfp_id)

    stage_one, stage_one_ready = _stage_one_text(rfp)
    stage_two, stage_two_ready = _structural_map_text(prior_research)
    with pipeline_step("fetch_pricing_guide"):
        guide_text, kb_sources = await _fetch_guide_context(rfp, stage_two)

    from app.services.pricing_rate_card_store import build_stable_rate_card
    from app.services.pricing_rate_binding import bind_budget_line_items_to_rate_card
    from app.services.pricing_contract_builder import (
        build_pricing_contract,
        format_pricing_contract_for_prompt,
    )
    from app.services.commission_budget_sanitizer import sanitize_commission_budget

    rate_card = build_stable_rate_card(guide_text)
    for warn in rate_card.warnings:
        logger.info("pricing_rate_card_warning rfp_id=%s warn=%s", rfp_id, warn)

    guide_missing = bool((guide_text or "").startswith("(No 00_Guide_Pricing"))
    pinned_ok = any(
        (src or "").casefold().startswith("00_guide_pricing") for src in (kb_sources or [])
    )
    try:
        assert_rate_card_usable(
            rate_card=rate_card,
            guide_text=guide_text or "",
            guide_missing=guide_missing,
        )
    except ProposalError:
        if guide_missing or pinned_ok:
            # Pinned full doc already loaded — re-search won't change parse failures.
            raise
        # One guided re-fetch with menu-dense focus, then fail closed if still junk.
        logger.warning(
            "pricing_rate_card_unusable_retry rfp_id=%s rates=%s — re-fetching guide",
            rfp_id,
            len(rate_card.rates or []),
        )
        with pipeline_step("fetch_pricing_guide_retry"):
            guide_text, kb_sources = await _fetch_guide_context(
                rfp,
                stage_two,
                focus_hint=(
                    "menu 1.1 2.1 3.1 4.4 5.3 5.4 9.1 9.2 "
                    "Low Average High tier ranges (Avg: $)"
                ),
            )
        rate_card = build_stable_rate_card(guide_text)
        guide_missing = bool((guide_text or "").startswith("(No 00_Guide_Pricing"))
        assert_rate_card_usable(
            rate_card=rate_card,
            guide_text=guide_text or "",
            guide_missing=guide_missing,
        )
        step_trace(
            "pricing_rate_card_refetch_ok",
            rfp_id=rfp_id,
            rate_card_rates=len(rate_card.rates or []),
            guide_chars=len(guide_text or ""),
        )

    pricing_contract = build_pricing_contract(
        stage_one_text="\n".join(
            part
            for part in (
                stage_one,
                (prior_research.budget.media_spend_notes if prior_research and prior_research.budget else "") or "",
                (prior_research.budget.rfp_budget_notes if prior_research and prior_research.budget else "") or "",
            )
            if part
        ),
        rfp_text=rfp_context,
    )
    contract_prompt = format_pricing_contract_for_prompt(pricing_contract)
    step_trace(
        "pricing_inputs_ready",
        rfp_id=rfp_id,
        stage_one_ready=stage_one_ready,
        stage_two_ready=stage_two_ready,
        guide_chars=len(guide_text or ""),
        guide_missing=bool((guide_text or "").startswith("(No 00_Guide_Pricing")),
        rate_card_rates=len(rate_card.rates or []),
        rate_card_warnings=len(rate_card.warnings or []),
        contract_fee_model=str(getattr(pricing_contract, "fee_model", "") or ""),
        contract_confidence=getattr(pricing_contract, "confidence", None),
        kb_source_count=len(kb_sources or []),
    )

    manuscript_digest = ""
    try:
        from app.services.proposal_repository import aget_proposal_draft

        draft = await aget_proposal_draft(rfp_id)
        manuscript_digest = _manuscript_pricing_digest(draft)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Manuscript pricing digest skipped for %s: %s", rfp_id, exc)

    user_content = "\n".join(
        [
            f"RFP: {rfp.title}",
            f"Client: {rfp.client} | Sector: {rfp.sector} | Location: {rfp.location or '(n/a)'}",
            f"Estimated value (metadata): {rfp.estimated_value or '(not set)'}",
            f"\n=== Stage 1 Go/No-Go ===\n{stage_one[:12_000]}",
            f"\n=== Stage 2 Structural map (deliverables) ===\n{stage_two[:10_000]}",
            f"\n=== 00_Guide_Pricing (KB) ===\n{guide_text}",
            f"\n{contract_prompt}",
            (
                f"\n=== Manuscript approach + current budget (PRICE MUST FUND THESE PHASES) ===\n"
                f"{manuscript_digest[:14_000]}"
                if manuscript_digest
                else "\n=== Manuscript approach + current budget ===\n(none yet)"
            ),
            f"\n=== RFP excerpt ===\n{rfp_context[:28_000]}",
            "\n=== CRITICAL REMINDERS ===\n"
            "- Do NOT double-count the same guide line across two phases.\n"
            "- Phase fees must fund the depth described in Technical Ability / approach.\n"
            "- Discounted-below-tier lines must be labeled as scoped/discounted, not 'clean Average'.\n"
            "- Produce a single bottom-line total (lumpSumTotal / agencyRevenueEstimate).\n"
            "- Client-facing copy MUST be short: 2–4 sentence scopeSummary, brief Terms, "
            "plain fee names — no long math essays or internal Sonja/guide jargon in narrative fields.",
        ]
    )
    from app.services.proposal_budget_format_judge import rfp_indicates_fixed_pricing_table

    if rfp_indicates_fixed_pricing_table(rfp_context):
        user_content += (
            "\n\n=== FIXED PRICING INSTRUMENT (deterministic) ===\n"
            "This RFP requires FIXED pricing via Pricing Table / Cost Proposal attachment — "
            "NOT a scored hourly labor-category table. Use budgetFormat=phased with "
            "project/deliverable line items priced from 00_Guide_Pricing tier ranges."
        )

    messages = [
        {"role": "system", "content": STAGE3_BUDGET_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        with pipeline_step("budget_llm_json"):
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
        step_trace(
            "pricing_llm_first_pass_failed",
            rfp_id=rfp_id,
            error_type=exc.__class__.__name__,
            error_message=str(exc)[:300],
        )
        compact_user = (
            user_content
            + "\n\nIMPORTANT: Return COMPACT JSON only. Maximum 20 lineItems. "
            "Keep rfpBudgetNotes under 500 characters. No markdown or commentary. "
            "agencyRevenueEstimate MUST be > 0 when mediaSpendAnnual is evidenced or "
            "non-commission agency fees apply. When mediaSpendAnnual is null, do NOT invent "
            "commission dollars — use MANUAL FILL placeholders instead."
        )
        with pipeline_step("budget_llm_json_compact_retry"):
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

    line_items = _parse_line_items_from_raw(raw)
    used_skeleton = False
    if not line_items:
        logger.warning(
            "Stage 3 budget returned no lineItems (keys=%s)",
            sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
        )
        step_trace(
            "pricing_llm_missing_line_items_retry",
            rfp_id=rfp_id,
            keys=sorted(raw.keys()) if isinstance(raw, dict) else [],
        )
        with pipeline_step("budget_llm_json_line_items_retry"):
            raw, provider = await llm.chat_json(
                [
                    *messages,
                    {"role": "assistant", "content": json.dumps(raw)},
                    {"role": "user", "content": _LINE_ITEMS_RETRY_USER},
                ],
                max_tokens=8192,
                temperature=0.2,
            )
        line_items = _parse_line_items_from_raw(raw)
        used_skeleton = False
        if not line_items:
            line_items = skeleton_line_items_from_rate_card(rate_card)
            used_skeleton = True
            flags.append(
                "[PRICING FLAG: Model omitted lineItems after retry — used 00_Guide_Pricing "
                "skeleton. Refine in Budget before submit.]"
            )
            logger.warning(
                "Stage 3 budget used rate-card skeleton (%d rows) after empty lineItems",
                len(line_items),
            )
            step_trace(
                "pricing_llm_line_items_skeleton_fallback",
                rfp_id=rfp_id,
                rows=len(line_items),
            )

    confidence = int(raw.get("confidence") or 0)
    if not stage_one_ready or not stage_two_ready:
        confidence = min(confidence, 50)
    if guide_text.startswith("(No 00_Guide_Pricing"):
        confidence = min(confidence, 40)
    if used_skeleton:
        confidence = min(confidence, 35)

    extras = parse_budget_extras(raw)
    from app.services.evidence_trust.rfp_money_constraints import (
        apply_constraints_to_budget_fields,
        extract_rfp_money_constraints_with_llm_fallback,
    )
    forced_format = str(raw.get("budgetFormat") or "phased")
    try:
        from app.services.proposal_budget_format_judge import (
            align_budget_format_to_judgment,
            judge_rfp_budget_format,
        )

        judgment = await judge_rfp_budget_format(rfp_context)
        aligned, changed = align_budget_format_to_judgment(forced_format, judgment)
        if changed:
            logger.info(
                "Pricing budgetFormat judged %s → %s (%s)",
                forced_format,
                aligned,
                judgment.reason[:120],
            )
            forced_format = aligned
            flags.append(
                f"[PRICING FLAG: Cost instrument aligned to RFP judge → {aligned}]"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Budget format judge skipped during pricing for %s: %s", rfp_id, exc)

    money_constraints = await extract_rfp_money_constraints_with_llm_fallback(rfp_context)
    budget = ProposalBudget(
        rfpId=rfp_id,
        rfpBudgetCap=_parse_budget_cap(raw.get("rfpBudgetCap")),
        rfpBudgetNotes=str(raw.get("rfpBudgetNotes") or "")[:4000],
        feeStructure=str(raw.get("feeStructure") or ""),
        pricingTier=str(raw.get("pricingTier") or "Average"),
        budgetFormat=forced_format,
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
    # Deterministic RFP money authority wins over LLM-invented caps matching own total.
    budget = apply_constraints_to_budget_fields(budget, money_constraints)

    # Decision Guide: cost ≥25% → Low tier (never leave Average on a 35% price RFP).
    try:
        from app.services.proposal_integrity_guards import (
            enforce_pricing_tier_for_cost_weight,
            infer_cost_weight_pct,
        )

        weight = infer_cost_weight_pct(rfp_context, prior_research)
        budget, tier_logs = enforce_pricing_tier_for_cost_weight(
            budget, cost_weight_pct=weight
        )
        for line in tier_logs:
            logger.info("Pricing tier guard for %s: %s", rfp_id, line)
    except Exception:
        logger.exception("Pricing tier cost-weight guard failed for %s", rfp_id)

    budget = sanitize_commission_budget(budget, pricing_contract)
    # Bind before editor so MANUAL FILL commission placeholders do not trip unbound
    # XOR checks against still-unbound labor lines inside assert_budget_canonical.
    budget = bind_budget_line_items_to_rate_card(budget, rate_card)
    step_trace(
        "pricing_bound_to_rate_card",
        rfp_id=rfp_id,
        rate_card_rates=len(rate_card.rates or []),
        **summarize_budget(budget),
    )
    for warn in rate_card.warnings:
        flag = f"[PRICING FLAG: rate card] {warn}"
        if flag not in (budget.pricing_flags or []):
            budget = budget.model_copy(
                update={"pricing_flags": [*(budget.pricing_flags or []), flag]}
            )

    budget = run_budget_editor_pass(
        budget,
        rfp_sections=prior_research.rfp_sections if prior_research else [],
        rfp_context=rfp_context,
        rate_card=rate_card,
    )

    budget, coerce_logs = coerce_budget_to_phased_from_guide(
        budget, rate_card, rfp_text=rfp_context
    )
    for line in coerce_logs:
        logger.info("Pricing coerce for %s: %s", rfp_id, line)
    if coerce_logs:
        budget = run_budget_editor_pass(
            budget,
            rfp_sections=prior_research.rfp_sections if prior_research else [],
            rfp_context=rfp_context,
            rate_card=rate_card,
        )

    # Phase 3.5a — adversarial grounding audit (RFP requirement + guide SKU per line).
    grounding_rows, audit_flags = await _run_budget_grounding_audit(
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_context=rfp_context,
        stage_two=stage_two,
        guide_text=guide_text,
        budget=budget,
    )
    if grounding_rows:
        budget = budget.model_copy(update={"line_item_grounding": grounding_rows})
    if audit_flags:
        existing_flags = list(budget.pricing_flags or [])
        for flag in audit_flags:
            msg = f"[PRICING FLAG:{flag.severity.upper()}] {flag.concern}"
            if flag.line_item_id:
                msg += f" (line {flag.line_item_id})"
            if msg not in existing_flags:
                existing_flags.append(msg)
        budget = budget.model_copy(
            update={"pricing_flags": existing_flags, "pricing_audit_flags": audit_flags}
        )
    step_trace(
        "pricing_grounding_audit",
        rfp_id=rfp_id,
        grounding_rows=len(grounding_rows or []),
        audit_flags=len(audit_flags or []),
        **summarize_budget(budget),
    )

    from app.services.proposal_budget_content import (
        normalize_fixed_pricing_narrative,
        prepare_budget_for_client_display,
    )

    budget = prepare_budget_for_client_display(budget)
    budget = normalize_fixed_pricing_narrative(budget, rfp_text=rfp_context)

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

    rate_card_payload = rate_card.model_dump(by_alias=True)
    contract_payload = pricing_contract.model_dump(by_alias=True)

    if prior_research:
        research = prior_research.model_copy(
            update={
                "budget": budget,
                "pricing_rate_card": rate_card_payload,
                "pricing_contract": contract_payload,
                "updated_at": now,
                "provider": provider,
            }
        )
    else:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            budget=budget,
            pricingRateCard=rate_card_payload,
            pricingContract=contract_payload,
            updatedAt=now,
            provider=provider,
        )
    await asave_research_cache(research)

    logger.info(
        "Stage 3 budget for %s: %d line items, tier=%s, format=%s, confidence=%d, rate_card=%d",
        rfp_id,
        len(budget.line_items),
        budget.pricing_tier,
        budget.budget_format,
        budget.confidence,
        len(rate_card.rates),
    )
    step_trace(
        "pricing_budget_persisted",
        rfp_id=rfp_id,
        provider=str(provider or ""),
        rate_card_rates=len(rate_card.rates or []),
        **summarize_budget(budget),
    )
    return budget, research


async def reconcile_cached_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Re-run deterministic budget editor on cached budget (no LLM regen)."""
    rfp, _content, rfp_context = load_rfp_for_proposal(rfp_id)
    research = await aget_research_cache(rfp_id)
    if not research or not research.budget:
        raise ProposalError(
            "No cached budget to reconcile. Run Phase 3.5 budget generation first.",
            status_code=400,
        )

    from app.models.pricing_contract import PricingContract
    from app.services.commission_budget_sanitizer import sanitize_commission_budget
    from app.services.pricing_contract_builder import build_pricing_contract
    from app.services.pricing_rate_binding import bind_budget_line_items_to_rate_card
    from app.services.pricing_rate_card_store import build_stable_rate_card

    stage_one, _ = _stage_one_text(rfp)
    prior_notes = ""
    if research.budget:
        prior_notes = "\n".join(
            part
            for part in (
                research.budget.media_spend_notes or "",
                research.budget.rfp_budget_notes or "",
                research.budget.fee_structure or "",
            )
            if part
        )
    if research.pricing_contract:
        try:
            contract = PricingContract.model_validate(research.pricing_contract)
        except Exception:  # noqa: BLE001
            logger.warning(
                "pricing_contract invalid on cache for %s — rebuilding", rfp_id
            )
            contract = build_pricing_contract(
                stage_one_text=f"{stage_one}\n{prior_notes}",
                rfp_text=rfp_context,
            )
    else:
        contract = build_pricing_contract(
            stage_one_text=f"{stage_one}\n{prior_notes}",
            rfp_text=rfp_context,
        )

    budget = sanitize_commission_budget(research.budget, contract)
    rate_card = None
    if research.pricing_rate_card:
        try:
            from app.models.pricing_rate_card import PricingRateCard

            rate_card = PricingRateCard.model_validate(research.pricing_rate_card)
        except Exception:  # noqa: BLE001
            rate_card = None
    if rate_card is None:
        guide_text, _kb = await _fetch_guide_context(rfp, _structural_map_text(research)[0])
        rate_card = build_stable_rate_card(guide_text)
    budget = bind_budget_line_items_to_rate_card(budget, rate_card)
    budget = run_budget_editor_pass(
        budget,
        rfp_sections=research.rfp_sections,
        rfp_context=rfp_context,
        rate_card=rate_card,
    )
    now = datetime.now(timezone.utc).isoformat()
    research = research.model_copy(
        update={
            "budget": budget,
            "pricing_contract": contract.model_dump(by_alias=True),
            "updated_at": now,
        }
    )
    await asave_research_cache(research)
    logger.info(
        "Budget reconciled for %s: revenue=%s, lump=%s, %d line items",
        rfp_id,
        budget.agency_revenue_estimate,
        budget.lump_sum_total,
        len(budget.line_items),
    )
    return budget, research
