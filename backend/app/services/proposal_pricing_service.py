"""Stage 3 budget: 00_Guide_Pricing + Stage 1/2 context + RFP excerpt."""

from __future__ import annotations

import json
import logging
import re
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
# Role billable card — pin by Supermemory *title* first (UI: Labor Cost).
LABOR_RATE_CARD_TITLES: tuple[str, ...] = (
    "Labor Cost",
    "Labor Costs",
)
# Filename fallback when title lookup misses (rename / (1) variants).
LABOR_RATE_CARD_FILE_NAMES: tuple[str, ...] = (
    "Agency Role Rates & Cost Table. (1).docx",
    "Agency Role Rates & Cost Table.docx",
)
PINNED_LABOR_CHAR_LIMIT = 40_000
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


from app.services.proposal_drafting_prompts import GLOBAL_AGENT_PROMPT_RULES

STAGE3_BUDGET_PROMPT = """You are zö agency's Stage 3 Budget assistant. Build a complete, defensible budget using ONLY:
""" + GLOBAL_AGENT_PROMPT_RULES + """

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
6. CONTRACT / FUNDING HORIZON (any RFP): Read the period of performance, base term, option
   years, and any fixed funding/performance end date. Price and narrate for THAT horizon —
   do not assume a one-year engagement when THIS RFP is multi-year (or the reverse). When
   award start is TBD but money/performance stops on a fixed calendar date, say so in
   scopeSummary / optionTermNotes and do not invent a rigid Month-N grid past that end.

PHASE 2 — Pick ONE pricing tier (Low / Average / High) for the entire proposal.

PHASE 3 — Map every RFP deliverable to a Pricing Guide line item as a PHASE / DELIVERABLE row:
- CRITICAL: You MUST include every single requirement and deliverable requested in the RFP. If you cannot find a matching Pricing Guide item in the KB for an RFP requirement, DO NOT omit it. You MUST include it as a line item, set `isManualFill=true` or use a `[VERIFY: Missing pricing]` tag, and add a pricing flag. Never silently skip a requirement.
- Map by meaning to Guide menu ids. Prefer Category 02 messaging / campaign strategy rows over
  Category 05 website / digital retainers when the RFP asks for campaign strategy & messaging.
  Do NOT collapse distinct video + print + digital production asks into one SKU.
- description MUST name the phase + deliverable (e.g. "Phase 1 Discovery — Stakeholder interviews
  covering the RFP communications-plan ask"). NEVER invent "RFP §X Item Y" citations.
  NEVER use bare "Strategy Lead — Name" as the only description.
- category = phase name (Discovery / Strategy / Tactical Plan / Roadmap / etc.)
- namedPerson / roleTitle are optional staffing notes — not a substitute for the deliverable label.
- Prefer budgetFormat=phased (or service_menu) UNLESS THIS RFP explicitly requires:
  (a) personnel_loading — hourly rates BY ROLE / labor category (often with Year-2 / Year-3 % increases), OR
  (b) blended_rate_form — a single hourly / monthly / annual block.
- When THIS RFP asks for a role-by-role hourly table OR a complete hourly rate schedule
  by classification (even alongside phased / NTE fees), you MUST include that schedule.
  Use billable $/hr rows from the KB labor / role rate excerpts provided (cite the
  source filename). Never invent classifications or dollars. Never use Internal Rate
  or Raw floor cost columns — only Billable Rate for the client schedule.
  If the RFP also wants phased/NTE fees, keep budgetFormat=phased for fee detail AND
  still emit unit=hour lineItems (or verifiedRates) for every KB billable role so the
  Cost tab can render the mandatory rate schedule.
- When THIS RFP's scored instrument is ONLY the hourly table (no fixed-fee ask),
  budgetFormat MUST be personnel_loading.
  Emit one agency_fee lineItem per RFP-named role with unit=hours (quantity may be 1 for rate display),
  rate = KB labor-category / role billable hourly, roleTitle = exact RFP role label. Put Year-2 / Year-3 % in
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
- Use WORK / labor-category hourly rates from the === LABOR COST (pinned role billable card) ===
  block (Billable column only) when present; otherwise 00_Guide_Pricing labor-category rows.
- NEVER invent individual ZO team-member hourly rates or "blended" $/hr ranges.
- NEVER use Guide menu SKUs (e.g. 4.1, 5.1 deliverable tiers) as person/role hourly rates.
- NEVER put Internal Rate / Raw Floor / "internal billable" figures in client-facing copy.
- namedPerson may appear as a staffing note only — the rate MUST cite a Labor Cost / guide labor category, not a person.
- If the RFP demands named-person loaded rates and KB has none: leave rate as
  [PRICING FLAG: Sonja approve loaded rate — {role}] or [VERIFY: named person hourly rate — not in KB]
  — do NOT fabricate verifiedRates.hourlyRate for a personName.
- Commission-model RFPs STILL need this transparency table — commission is total compensation but evaluators require hours

NAMED-PERSON RATES BAN:
- verifiedRates should be EMPTY unless a source string cites LABOR COST / labor-category Billable text verbatim.
- Do not pull burdened person rates from 07_FIN prior proposals.
- When Guide 6.1 Traditional Media (85/15) is in the pricing excerpts, use that verified commission —
  do NOT VERIFY a media buy commission that the Guide already states.

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
   "professional fees." Professional fees = sum of agency_fee line items ONLY.
   Never set Professional fees equal to fees+travel or to the grand total.
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
- qualifyingLanguage: four markdown blocks — ### Investment Framing, ### Scope Protection,
  ### Reimbursable Expenses, ### Revision Rounds. Copy the 00_Guide_Pricing USE VERBATIM
  paragraphs exactly (abide by those terms; discovery progresses and priorities sharpen;
  mileage at current IRS rate / photography/videography location fees and permits /
  specialized software licenses; three rounds of review). NEVER paraphrase those four.
  Optional: add ONE RFP-specific reimbursable note AFTER the verbatim reimbursable sentence
  (do not replace it). NEVER a Component|Share mix table when Fee Detail by Phase exists.
- rfpBudgetNotes: optional one short paragraph OR empty — never a multi-page methodology essay.
- lineItem descriptions: phase + deliverable tied to RFP items. Put guide citations in rateSource only.
  When the manuscript / RFP describes numbered Implementation phases, cite those SAME
  phase names/numbers in each lineItem description (do not call Strategy work "Phase 1"
  if Implementation already uses Phase 1 for discovery and Phase 2 for strategy build).
  If Launch / account management is absorbed into another fee line, say so explicitly
  in that line's description ("includes Phase 3 launch trafficking" / "includes ongoing
  account management cadence") — never leave a promised workstream with no fee home.
- Do NOT write long "build-out" prose that re-explains every math step in the section narrative.
- optionTermNotes: client language only ("proposed fees") — never "agency revenue estimate".

PHASE 6b — qualifyingLanguage MUST include all four USE VERBATIM Pricing Guide blocks as
markdown headings (not paraphrased bullets): Investment Framing, Scope Protection,
Reimbursable Expenses, Revision Rounds. qualifyingLanguage MUST use the SAME pricingTier
selected in PHASE 2 — never mention a different tier as "baseline."

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

CRITICAL — RFP-STATED MINIMUM BUDGET (money left on the table if wrong):
- If the RFP states a MINIMUM budgeted amount, a budget range, or "no less than $X"
  for the contract/project (NOT insurance limits, bonds, or vendor revenue thresholds),
  the proposed total must land AT OR ABOVE that figure.
- Reach it by scoping the work to the depth the RFP actually calls for — more content,
  a fuller term, real measurement and reporting — never by padding rows or inflating
  rates out of their 00_Guide_Pricing band.
- A bid far under a stated minimum reads as under-reading the scope. Do not "save the
  buyer money" against a floor they published.

CRITICAL — LARGE NTE + MEDIA / MESSAGING CAMPAIGN (same failure mode as under-minimum):
- When the RFP requires paid media buying / placement / advertising buy AND states a hard
  NTE / total contract ceiling (or program media envelope), do NOT emit a thin
  Discovery → Strategy → Creative professional-fee-only quote that uses a small fraction
  of the NTE while omitting media and multi-year workstreams.
- Cover EVERY required workstream across the FULL term (plan, research, creative, video/
  PSA, toolkit, stakeholder/listening, analytics, PM) as agency_fee rows from the guide.
- Add traditional + digital media as lineItemType=client_passthrough so
  agencyRevenueEstimate + clientMediaPassthrough approaches the NTE (typically ≥ ~60%
  for a statewide multi-year media campaign) and never exceeds it.
- Use the guide's 85/15 commission model when PricingContract / guide supports it —
  only the agency share of media is agency revenue; placements stay passthrough.
- If media dollar volume is not stated, allocate remaining NTE after honest agency fees
  to media passthrough with [PRICING FLAG: media buy estimate — Sonja confirm] — never
  omit media when buying/placement is in scope.
- Still: do not invent guide rates outside bands; do not invent deliverables the RFP
  does not ask for.

CRITICAL — HARD CAP / YEAR ALLOCATIONS (submission disqualifier if wrong):
- If the RFP states a maximum compensation / NTE / total proposed price ceiling (e.g. $2,950,000),
  set rfpBudgetCap to that number and keep agencyRevenueEstimate + lumpSumTotal AT OR UNDER it.
  For media-campaign bids, also keep (agency fees + clientMediaPassthrough) ≤ rfpBudgetCap.
- Yearly "Annual Allocation Year 1/2/3" (or similar) figures are the BUDGET ENVELOPE, not cost lines.
  NEVER add them as lineItems — that double-counts and exceeds the hard cap.
- lineItems = billable work only (Discovery, Strategy, Content, Digital, PM, etc.) that SUM to ≤ rfpBudgetCap
  (plus separate client_passthrough media rows when media buying is required — those count toward
  totalClientInvoicing / NTE but not agencyRevenueEstimate).
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


async def _fetch_pinned_labor_rate_card() -> tuple[str, list[str]] | None:
    """Load the Agency Role Rates / Labor Cost card (full text) for billable $/hr.

    Prefer Supermemory document *title* (Labor Cost). Fall back to known
    filenames so renames still resolve. Result is injected into Stage 3 context
    as an explicit retrieve chunk — not fuzzy-search-only.
    """
    if not supermemory.is_configured():
        return None

    async def _content_from_doc(document: dict, *, label: str) -> tuple[str, str] | None:
        custom_id = supermemory.document_fetch_key(document)
        if not custom_id:
            logger.warning("labor_rate_card_pin_missing_fetch_key label=%s", label)
            return None
        content = await supermemory.get_document_content(custom_id=custom_id)
        if not (content or "").strip():
            logger.warning(
                "labor_rate_card_pin_empty_content label=%s custom_id=%s",
                label,
                custom_id,
            )
            return None
        text = content.strip()
        if len(text) > PINNED_LABOR_CHAR_LIMIT:
            text = text[:PINNED_LABOR_CHAR_LIMIT]
        return text, label

    for title in LABOR_RATE_CARD_TITLES:
        try:
            document = await supermemory.find_document_by_title(title)
            if not document:
                continue
            loaded = await _content_from_doc(document, label=title)
            if loaded is None:
                continue
            text, label = loaded
            logger.info(
                "labor_rate_card_pinned_by_title title=%s chars=%s",
                label,
                len(text),
            )
            return text, [label]
        except supermemory.SupermemoryError as exc:
            logger.warning(
                "labor_rate_card_title_pin_failed title=%s error=%s",
                title,
                exc,
            )

    for file_name in LABOR_RATE_CARD_FILE_NAMES:
        try:
            document = await supermemory.find_document_by_file_name(file_name)
            if not document:
                continue
            loaded = await _content_from_doc(document, label=file_name)
            if loaded is None:
                continue
            text, label = loaded
            logger.info(
                "labor_rate_card_pinned_by_filename file_name=%s chars=%s",
                label,
                len(text),
            )
            return text, [label]
        except supermemory.SupermemoryError as exc:
            logger.warning(
                "labor_rate_card_filename_pin_failed file_name=%s error=%s",
                file_name,
                exc,
            )

    logger.warning("labor_rate_card_pin_miss — no Labor Cost title/filename hit")
    return None


async def _fetch_labor_role_rate_context(
    rfp: RfpRecord,
    *,
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """KB-wide retrieve of role / classification billable hourly tables.

    Searches pricing + reference (and unfiltered). When a hit filename looks like
    a role/labor rate card, upgrade that hit to the **full** indexed document so
    the billable table is not truncated by chunk merge.
    """
    if not supermemory.is_configured():
        return "", []

    hint = (focus_hint or "")[:200]
    queries = [
        "billable rate by role labor classification Account Manager Creative Director hourly USD",
        "role rates cost table billable per hour copywriter art director agency director",
        "hourly labor category rates Project Manager Digital Team Programming Finance",
    ]
    if hint:
        queries.insert(0, f"billable hourly role rates {hint}")

    chunks: list[str] = []
    sources: list[str] = []
    seen_chunk_keys: set[str] = set()
    for query in queries:
        for category in ("pricing", "reference", None):
            text, srcs = await search_knowledge_base(
                query,
                limit=6,
                category=category,
                max_chars=10_000,
                rfp_client=rfp.client or "",
                rfp_title=rfp.title or "",
            )
            if text and not text.startswith("("):
                key = text[:180]
                if key not in seen_chunk_keys:
                    seen_chunk_keys.add(key)
                    chunks.append(text)
            for src in srcs:
                if src not in sources:
                    sources.append(src)
            if len("\n".join(chunks)) >= 18_000:
                break
        if len("\n".join(chunks)) >= 18_000:
            break

    # Upgrade role/labor rate-card hits to full document text (search chunks truncate).
    full_docs: list[str] = []
    upgraded: list[str] = []
    for src in sources[:12]:
        name = (src or "").strip()
        if not name:
            continue
        if not re.search(
            r"(?i)role\s+rates?|labor\s+cost|billable\s+rate|rate\s+card|cost\s+table",
            name,
        ):
            continue
        # Skip the menu guide — already pinned separately.
        if re.search(r"(?i)00_guide_pricing", name):
            continue
        try:
            document = await supermemory.find_document_by_file_name(name)
            if not document:
                continue
            custom_id = supermemory.document_fetch_key(document)
            if not custom_id:
                continue
            content = await supermemory.get_document_content(custom_id=custom_id)
            body = (content or "").strip()
            if len(body) < 200:
                continue
            full_docs.append(f"[full doc: {name}]\n{body[:12_000]}")
            upgraded.append(name)
            logger.info(
                "labor_role_rates_full_doc rfp_id=%s file_name=%s chars=%s",
                rfp.id,
                name,
                len(body),
            )
        except Exception:
            logger.warning(
                "labor_role_rates_full_doc_failed rfp_id=%s file_name=%s",
                rfp.id,
                name,
                exc_info=True,
            )

    parts = [*full_docs, *chunks]
    combined = "\n\n---\n\n".join(parts)[:24_000]
    if combined:
        logger.info(
            "labor_role_rates_kb_hits rfp_id=%s sources=%s upgraded=%s chars=%s",
            rfp.id,
            sources[:8],
            upgraded[:6],
            len(combined),
        )
    return combined, sources


async def _fetch_guide_context(
    rfp: RfpRecord,
    stage_two: str,
    *,
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """Retrieve 00_Guide_Pricing (pin first) plus KB labor/role billable rates."""
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    guide_text = ""
    sources: list[str] = []
    pinned = await _fetch_pinned_pricing_guide()
    if pinned is not None:
        guide_text, sources = pinned
    else:
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
        seen_chunk_keys: set[str] = set()
        for query in queries:
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

        guide_text = "\n\n---\n\n".join(chunks)[:GUIDE_SEARCH_CHAR_LIMIT]
        if not guide_text.strip():
            guide_text = "(No 00_Guide_Pricing content in KB — ingest pricing guide.)"

    # Pin Labor Cost / Agency Role Rates card into the retrieve bundle first so
    # billable $/hr is always present for parse + Stage 3 (not fuzzy-only).
    pinned_labor = await _fetch_pinned_labor_rate_card()
    if pinned_labor is not None:
        labor_pin_text, labor_pin_srcs = pinned_labor
        guide_text = (
            f"{guide_text.rstrip()}\n\n"
            "=== LABOR COST (pinned role billable card) ===\n"
            f"{labor_pin_text.strip()}"
        )
        for src in labor_pin_srcs:
            if src not in sources:
                sources.append(src)

    # Supplement with KB search for classification / role billable hours when
    # the pin is thin or missing alternate role tables.
    labor_text, labor_srcs = await _fetch_labor_role_rate_context(
        rfp, focus_hint=focus_hint or stage_two[:200]
    )
    if labor_text.strip():
        guide_text = (
            f"{guide_text.rstrip()}\n\n"
            "=== KB labor / role billable rates (search — cite source filenames) ===\n"
            f"{labor_text.strip()}"
        )
        for src in labor_srcs:
            if src not in sources:
                sources.append(src)

    return guide_text, sources


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


_LINE_ITEMS_RETRY_USER = (
    "Your previous JSON omitted a usable lineItems array (required for submission). "
    "Return the COMPLETE budget JSON again. lineItems MUST be a non-empty array with "
    "at least 5 rows covering Discovery, Strategy, Creative, Digital/PM, and Travel when "
    "applicable. Each item needs id, description, category, lineItemType, quantity, rate, "
    "extended (numbers). agencyRevenueEstimate and lumpSumTotal MUST match summed agency fees."
)


def _normalize_qualifying_language(raw: Any) -> str:
    from app.services.proposal_budget_content import (
        force_pricing_guide_verbatim_qualifying_language,
    )

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
        text = "\n\n".join(parts)
    else:
        text = str(raw or "").strip()
    return force_pricing_guide_verbatim_qualifying_language(text)


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
                    "labor cost",
                    "labor costs",
                    "labor category",
                    "role billable",
                    "agency role rates",
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
    """Feed Stage 3 the full manuscript (except hollow shells) so THIS RFP's
    layout / billing / NTE language from any tab can shape the budget.

    No keyword synonym filter — the pricing LLM judges meaning from the text.
    """
    from app.models.proposal import ProposalDraft

    if not isinstance(draft, ProposalDraft):
        return ""
    parts: list[str] = []
    total = 0
    max_chars = 28_000
    for section in draft.sections:
        title = (section.title or "").strip() or "Section"
        content = (section.content or "").strip()
        if not content:
            continue
        # Skip the Cost instrument tab itself (has Fee Detail + Proposed Investment).
        body_cf = content.casefold()
        if "fee detail" in body_cf and "proposed investment" in body_cf:
            continue
        piece = f"### {title}\n{content[:5000]}"
        if total + len(piece) > max_chars:
            remain = max_chars - total
            if remain < 400:
                break
            piece = piece[:remain]
        parts.append(piece)
        total += len(piece)
        if total >= max_chars:
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
            max_tokens=8192,
            temperature=0.0,
            node_name="stage35a_budget_grounding",
            reasoning_effort="low",
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


# --- Large NTE + media campaign under-utilization check ---------------------

_NTE_UTILIZATION_FLOOR = 0.55  # totalClientInvoicing / NTE


def budget_has_media_passthrough(budget: ProposalBudget) -> bool:
    """True when the ledger already includes client media passthrough dollars/rows."""
    if budget.client_media_passthrough is not None and float(
        budget.client_media_passthrough
    ) > 0:
        return True
    from app.services.proposal_budget_validation import infer_line_item_type

    for item in budget.line_items or []:
        if infer_line_item_type(item) == "client_passthrough" and float(
            item.extended or item.rate or 0
        ) > 0:
            return True
    return False


def budget_client_invoicing_total(budget: ProposalBudget) -> float:
    """Agency fees + media passthrough (or explicit totalClientInvoicing)."""
    explicit = budget.total_client_invoicing
    if explicit is not None and float(explicit) > 0:
        return float(explicit)
    agency = float(
        budget.agency_revenue_estimate
        or budget.agency_fee_subtotal
        or budget.lump_sum_total
        or 0
    )
    media = float(budget.client_media_passthrough or 0)
    if media <= 0:
        from app.services.proposal_budget_validation import infer_line_item_type

        for item in budget.line_items or []:
            if infer_line_item_type(item) == "client_passthrough":
                media += float(item.extended or item.rate or 0)
    return agency + media


def budget_underutilizes_large_nte(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
    floor_ratio: float = _NTE_UTILIZATION_FLOOR,
) -> bool:
    """True when a media-passthrough ledger still leaves most of a large NTE unused.

    Ledger-only (no RFP keyword scan). Agency-fee-only early-phase bids under a large
    NTE are handled by ``ensure_partial_nte_scope_disclosure``, not force-fill.
    """
    del rfp_text  # kept for call-site compatibility; meaning is not regex-scanned
    if not budget_has_media_passthrough(budget):
        return False
    cap = budget.rfp_budget_cap or budget.rfp_media_or_program_envelope
    if cap is None or float(cap) < 100_000:
        return False
    total = budget_client_invoicing_total(budget)
    return total < float(cap) * float(floor_ratio)


def _timeline_intel_from_research(
    prior_research: ProposalResearchCache | None,
) -> dict[str, Any]:
    """Pull timelineIntel from Phase 2 plan on the research cache (any RFP)."""
    if prior_research is None:
        return {}
    plan = getattr(prior_research, "proposal_execution_plan", None)
    if plan is None:
        return {}
    try:
        if hasattr(plan, "model_dump"):
            data = plan.model_dump(by_alias=True)
        elif isinstance(plan, dict):
            data = plan
        else:
            return {}
    except Exception:  # noqa: BLE001
        return {}
    opp = data.get("opportunity") if isinstance(data.get("opportunity"), dict) else {}
    und = (
        opp.get("understanding")
        if isinstance(opp.get("understanding"), dict)
        else {}
    )
    tl = und.get("timelineIntel")
    if isinstance(tl, dict):
        return tl
    tl = und.get("timeline_intel")
    return tl if isinstance(tl, dict) else {}


def _contract_horizon_block(prior_research: ProposalResearchCache | None) -> str:
    """Stage 3.5 prompt slice: price for THIS RFP's stated horizon (generic)."""
    tl = _timeline_intel_from_research(prior_research)
    if not tl:
        return ""

    def _pick(*keys: str) -> str:
        for key in keys:
            val = str(tl.get(key) or "").strip()
            if val:
                return val
        return ""

    horizon = _pick("contractHorizon", "contract_horizon")
    perf_end = _pick("performanceEnd", "performance_end")
    sched_auth = _pick("scheduleAuthority", "schedule_authority")
    options = _pick("optionPeriods", "option_periods")
    completion = _pick("completion")
    go_live = _pick("goLive", "go_live")

    lines: list[str] = []
    if horizon:
        lines.append(f"- Contract / funding horizon: {horizon}")
    if perf_end:
        lines.append(f"- Fixed performance / funding end: {perf_end}")
    elif completion:
        lines.append(f"- Stated completion / end: {completion}")
    if options:
        lines.append(f"- Option periods: {options}")
    if go_live:
        lines.append(f"- Go-live / peak cue: {go_live}")
    if sched_auth:
        lines.append(f"- Schedule authority: {sched_auth}")
    if not lines:
        return ""
    return (
        "\n=== CONTRACT / FUNDING HORIZON (from Phase 2 timelineIntel — any RFP) ===\n"
        + "\n".join(lines)
        + "\nPrice and narrate for THIS horizon. Do not assume a one-year engagement "
        "when the RFP is multi-year (or the reverse). When award start is TBD but "
        "money/performance stops on a fixed calendar date, say so in scopeSummary / "
        "optionTermNotes — do not invent a rigid Month-N grid past that end."
    )


def _opportunity_constraints_block(prior_research: ProposalResearchCache | None) -> str:
    """Typed PricingInstrument/DeliveryConstraints, else Phase-2 opportunity pack."""
    try:
        from app.services.pricing_delivery_context import (
            format_pricing_delivery_constraints_block,
        )

        block = format_pricing_delivery_constraints_block(
            prior_research, focus="budget"
        )
        if block:
            return f"\n{block}\n"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Opportunity hard constraints skipped: %s", exc)
    # Fallback: horizon-only (pre-opportunity wiring behavior).
    return _contract_horizon_block(prior_research)


async def generate_proposal_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Phase 3.5 budget: pricing plan v2 (asks -> LLM plan -> code checks -> render)."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    from app.services.go_no_go_service import combine_rfp_text
    from app.services.pricing_plan_service import generate_pricing_plan_budget

    _rfp, content, _rfp_context = load_rfp_for_proposal(rfp_id)
    prior_research = await aget_research_cache(rfp_id)
    full_rfp = combine_rfp_text(content.description, content.pdf_text)
    target = prior_research.target_budget_usd if prior_research else None
    budget = await generate_pricing_plan_budget(rfp_id, full_rfp, target_budget_usd=target)
    research = prior_research or ProposalResearchCache(
        rfpId=rfp_id, updatedAt=budget.updated_at, provider=budget.provider
    )
    research = research.model_copy(update={"budget": budget})
    await asave_research_cache(research)
    return budget, research


async def reconcile_cached_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Return the cached budget unchanged (v2 plans are final; legacy budgets are frozen)."""
    research = await aget_research_cache(rfp_id)
    if not research or not research.budget:
        raise ProposalError(
            "No cached budget to reconcile. Run Phase 3.5 budget generation first.",
            status_code=400,
        )
    return research.budget, research
