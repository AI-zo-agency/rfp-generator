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
from app.services.proposal_generator import ProposalError, _load_rfp_for_proposal
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

PHASE 5 — Budget page format (match RFP):
- phased: Phase 1/2/3 subtotals + project total
- personnel_loading: Team Member / Classification / Hourly Rate / Hours / Subtotal + NTE + Direct Expenses (hourly rates from KB 07_FIN/06_WON only)
- service_menu: per-unit or per-project rates by category

PHASE 6 — qualifyingLanguage MUST include all four blocks:
Investment Framing, Scope Protection, Reimbursable Expenses, Revision Rounds (use KB guide wording when present).

rateSource on each lineItem should cite the guide menu item (e.g. "5.3 — 00_Guide_Pricing Average tier").

Return ONLY JSON:
{
  "rfpBudgetCap": number|null,
  "rfpBudgetNotes": "string",
  "feeStructure": "string",
  "pricingTier": "Low|Average|High",
  "budgetFormat": "phased|personnel_loading|service_menu",
  "commissionModel": "string|null",
  "verifiedRates": [{"personName","role","hourlyRate","source"}],
  "lineItems": [{"id","category","description","namedPerson","roleTitle","unit","quantity","rate","extended","rateSource","notes"}],
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

lineItems must be a flat array (one row per line). Do not back-fill to the budget ceiling."""


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

    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)
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

    raw, provider = await llm.chat_json(
        [
            {"role": "system", "content": STAGE3_BUDGET_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=8192,
        temperature=0.2,
    )

    now = datetime.now(timezone.utc).isoformat()
    flags = [str(f) for f in (raw.get("pricingFlags") or []) if str(f).strip()]
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

    budget = ProposalBudget(
        rfpId=rfp_id,
        rfpBudgetCap=_parse_budget_cap(raw.get("rfpBudgetCap")),
        rfpBudgetNotes=str(raw.get("rfpBudgetNotes") or "")[:4000],
        feeStructure=str(raw.get("feeStructure") or ""),
        pricingTier=str(raw.get("pricingTier") or "Average"),
        budgetFormat=str(raw.get("budgetFormat") or "phased"),
        lineItems=line_items,
        tiers=_parse_tiers(raw.get("tiers")),
        recommendedTierId=raw.get("recommendedTierId"),
        agencyRevenueEstimate=(
            float(raw["agencyRevenueEstimate"])
            if isinstance(raw.get("agencyRevenueEstimate"), (int, float))
            else None
        ),
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
