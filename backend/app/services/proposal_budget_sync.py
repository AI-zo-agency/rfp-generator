"""Keep fee/pricing narrative aligned with the canonical Stage 3 budget."""

from __future__ import annotations

import logging
import re

from app.models.proposal import (
    BudgetNarrativeMismatch,
    ProposalBudget,
    ProposalDraft,
    ProposalSection,
)
from app.services import llm
from app.services.go_no_go_service import _assess_rfp_content, _build_rfp_context
from app.services.llm import LlmError
from app.services.proposal_budget_content import find_budget_section_index
from app.services.rfp_repository import get_rfp

logger = logging.getLogger(__name__)

_FEE_CONTENT_RE = re.compile(
    r"\b("
    r"pricing\s+tier|low\s+tier|average\s+tier|high\s+tier|"
    r"agency\s+revenue|commission|pass[\s-]*through|lump\s*sum|"
    r"investment\s+reflects|fee\s+structure|option\s+year|"
    r"\$[\d,]+"
    r")\b",
    re.I,
)

FEE_SLOT_PLAN_PROMPT = """You perform constrained fee narrative sync.

Input: proposal sections + canonical budget totals.
Task: detect dollar-claim sentences and map each to a known canonical field.
Do NOT rewrite full sections.

Return JSON only:
{
  "claims":[
    {
      "sectionId":"...",
      "sentence":"exact sentence from section",
      "claimType":"agency_fee|media_passthrough|direct_expenses|total_invoicing",
      "confidence":"high|medium|low",
      "note":""
    }
  ],
  "unmapped":[{"sectionId":"...","sentence":"...","reason":"..."}]
}
"""

FEE_GROUNDING_CHECK_PROMPT = """You are a grounding checker, not a writer.
Compare manuscript pricing claims against canonical budget values AND RFP money constraints.

Flag mismatches when:
1) A labeled fee / media / total claim does not match the canonical field.
2) The ledger exceeds an RFP hard fee NTE or program/media envelope listed below.
3) The manuscript calls the bid's own total the "RFP ceiling/allocation/cap" when that
   dollar is NOT an extracted RFP constraint (invented ceiling).

Do NOT flag tuition, sample reallocation examples, or RFP-stated envelopes that match
extracted constraints when used as RFP context (not as bid totals).

Return JSON only:
{
  "mismatches":[
    {
      "sectionId":"...",
      "sectionTitle":"...",
      "sentence":"...",
      "claimedField":"agency_fee|media_passthrough|direct_expenses|total_invoicing|rfp_ceiling_claim|rfp_authority",
      "canonicalValue":1234.56,
      "matches":false,
      "note":"why this sentence contradicts canonical value or RFP authority"
    }
  ]
}
"""


def _canonical_budget_facts(budget: ProposalBudget) -> str:
    from app.services.proposal_budget_validation import sum_line_items_extended

    line_subtotal = sum_line_items_extended(budget)
    direct = float(budget.direct_expenses_total or 0)
    agency_fee = float(budget.agency_fee_subtotal or line_subtotal)
    passthrough = float(budget.client_media_passthrough or 0)
    lines = [
        f"pricingTier: {budget.pricing_tier or 'Average'}",
        f"lineItemSum (all table rows): {budget.line_item_sum or line_subtotal}",
        f"agencyFeeSubtotal (zö fee rows only): {budget.agency_fee_subtotal or agency_fee}",
        f"clientMediaPassthrough (NOT agency revenue): {passthrough or 0}",
        f"directExpensesTotal: {direct}",
        (
            "agencyRevenueEstimate (USE FOR 'agency revenue' / commission / fee income): "
            f"{budget.agency_revenue_estimate}"
        ),
        (
            "totalClientInvoicing (media pass-through + agency fees — NOT agency revenue): "
            f"{budget.total_client_invoicing or (line_subtotal + direct)}"
        ),
        f"commissionRate: {budget.commission_rate}",
        f"lumpSumTotal: {budget.lump_sum_total}",
        f"feeStructure: {budget.fee_structure}",
        f"budgetFormat: {budget.budget_format}",
        f"commissionModel: {budget.commission_model or '(none)'}",
        f"rfpBudgetCap (hard fee NTE only): {budget.rfp_budget_cap}",
        f"rfpMediaOrProgramEnvelope: {budget.rfp_media_or_program_envelope}",
    ]
    if (budget.rfp_money_constraint_notes or "").strip():
        lines.append(
            "rfpMoneyConstraintNotes:\n" + budget.rfp_money_constraint_notes[:1200]
        )
    if budget.option_term_notes.strip():
        lines.append(f"optionTermNotes (canonical):\n{budget.option_term_notes[:1200]}")
    if budget.qualifying_language.strip():
        lines.append(f"qualifyingLanguage:\n{budget.qualifying_language[:2000]}")
    if budget.media_spend_notes.strip():
        lines.append(f"mediaSpendNotes:\n{budget.media_spend_notes[:800]}")
    revenue = float(budget.agency_revenue_estimate or 0)
    if revenue <= 0:
        lines.append(
            "CRITICAL: agencyRevenueEstimate is ZERO — do NOT write $0 in narrative; "
            "run budget reconcile or set commissionRate × clientMediaPassthrough first."
        )
    lines.append(
        "NEVER treat the proposal's own bid total as the RFP ceiling unless it equals "
        "rfpBudgetCap or rfpMediaOrProgramEnvelope above."
    )
    return "\n".join(lines)


def _needs_fee_sync(section: ProposalSection, budget_idx: int | None, index: int) -> bool:
    if budget_idx is not None and index == budget_idx:
        return False
    if not section.content.strip():
        return False
    return bool(_FEE_CONTENT_RE.search(section.content))


def _usd(value: float | None) -> str:
    if value is None:
        return "$0.00"
    if abs(value - round(value)) < 0.01:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def _canonical_slot_values(budget: ProposalBudget) -> dict[str, float]:
    from app.services.proposal_budget_content import canonical_budget_summary_figures

    figs = canonical_budget_summary_figures(budget)
    # agency_revenue substitutes only for a MISSING fee. A zero fee alongside
    # real travel is a true zero (all-travel budget) — backfilling it here
    # would sync "Agency fee: $3,500" into the manuscript for a budget whose
    # only line is $3,500 of travel. Mirrors reconcile_budget_summary_prose.
    agency = figs["agency_fee"]
    if agency <= 0 and figs["direct"] <= 0:
        agency = figs["agency_revenue"]
    return {
        "agency_fee": round(float(agency or 0), 2),
        "media_passthrough": round(float(figs["passthrough"] or 0), 2),
        "direct_expenses": round(float(figs["direct"] or 0), 2),
        "total_invoicing": round(float(figs["total"] or 0), 2),
    }


# Connects a budget label to its dollar figure — a colon ("Agency fee: $X") or
# natural sentence phrasing ("Agency fee is $X" / "...equals $X" / "...totals $X").
# Colon-only used to miss real client-facing prose like "Year 1 agency revenue
# is $325,242.66" (confirmed against tests/fixtures/manuscripts/
# cvvb_v1_duplication_budget), letting a mislabeled-but-canonical figure (the
# grand total, repeated under the agency-fee and pass-through labels too)
# through every deterministic check.
_LABEL_VALUE_CONNECTOR = (
    r"(?:\s*:\s*|\s+(?:is|are|was|equals?|totals?|comes?\s+to|amounts?\s+to)\s+)"
)
_USD_TOKEN = r"(\$[\d,]+(?:\.\d{2})?)"

_LABELLED_FEE_CLAIM_RES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?i)(?:Total\s+Year\s*1\s+agency\s+fee|Total\s+agency\s+(?:fee|revenue)|"
            r"Agency\s+(?:fee|revenue)(?:\s+estimate)?|Base-year\s+proposed\s+fees)"
            + _LABEL_VALUE_CONNECTOR
            + _USD_TOKEN
        ),
        "agency_fee",
    ),
    (
        re.compile(
            r"(?i)Client\s+media\s+pass-?through(?:\s*\([^)]*\))?"
            r"(?:\s+billed\s+at\s+net)?"
            + _LABEL_VALUE_CONNECTOR
            + _USD_TOKEN
        ),
        "media_passthrough",
    ),
    (
        re.compile(
            r"(?i)(?:Direct\s+travel\s*/\s*reimbursables|Direct\s+travel|"
            r"Estimated\s+reimbursable\s+travel)"
            + _LABEL_VALUE_CONNECTOR
            + _USD_TOKEN
        ),
        "direct_expenses",
    ),
    (
        re.compile(
            r"(?i)(?:Total\s+Year\s*1\s+client\s+invoicing|Total\s+client\s+invoicing|"
            r"Total\s+Year\s*1\s+investment|Total\s+proposed\s+investment|"
            r"Grand\s+total\s+client\s+invoicing)"
            + _LABEL_VALUE_CONNECTOR
            + _USD_TOKEN
        ),
        "total_invoicing",
    ),
]


def _parse_usd_token(text: str) -> float | None:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def collect_deterministic_budget_mismatches(
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> list[BudgetNarrativeMismatch]:
    """Label-aware dollar check — no LLM. Catches agency/passthrough/total swaps."""
    from app.services.evidence_trust.rfp_money_constraints import (
        collect_invented_ceiling_mismatches,
        collect_over_authority_flags,
    )

    slots = _canonical_slot_values(budget)
    out: list[BudgetNarrativeMismatch] = []
    seen: set[tuple[str, str, float]] = set()

    # Ledger vs RFP authority (even with no labeled fee claims).
    for flag in collect_over_authority_flags(budget):
        out.append(
            BudgetNarrativeMismatch(
                sectionId="budget",
                sectionTitle="Budget / RFP authority",
                sentence=flag[:500],
                claimedField="rfp_authority",
                canonicalValue=float(
                    budget.rfp_media_or_program_envelope
                    or budget.rfp_budget_cap
                    or 0
                ),
                matches=False,
                note=flag,
            )
        )

    if not any(v > 0 for v in slots.values()):
        return out

    for section in draft.sections:
        body = section.content or ""
        if not body.strip():
            continue
        for invented in collect_invented_ceiling_mismatches(
            body,
            budget=budget,
            section_id=section.id,
            section_title=section.title or "",
        ):
            key = (section.id, "rfp_ceiling_claim", float(invented.canonical_value or 0))
            # Deduplicate by sentence prefix
            sent_key = (section.id, "rfp_ceiling_claim", hash((invented.sentence or "")[:80]))
            if sent_key in seen:
                continue
            seen.add(sent_key)
            out.append(invented)

        for pattern, field in _LABELLED_FEE_CLAIM_RES:
            for match in pattern.finditer(body):
                claimed = _parse_usd_token(match.group(1))
                if claimed is None or claimed <= 0:
                    continue
                canonical = float(slots.get(field) or 0)
                if canonical <= 0:
                    continue
                tol = max(1.0, canonical * 0.02)
                if abs(claimed - canonical) <= tol:
                    continue
                key = (section.id, field, claimed)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    BudgetNarrativeMismatch(
                        sectionId=section.id,
                        sectionTitle=section.title or "",
                        sentence=match.group(0)[:240],
                        claimedField=field,
                        canonicalValue=canonical,
                        matches=False,
                        note=(
                            f"Labeled {field} claim {match.group(1)} does not match "
                            f"canonical {_usd(canonical)}"
                        ),
                    )
                )
    return out


# Bold markers land on either side of the colon depending on the renderer:
# render_budget_markdown emits "**Professional fees: $X**" while
# render_embedded_budget_table_markdown emits "**Professional fees:** $X".
# Both must parse — an unparsed label reads as a zero component below, which
# would defeat the single-component carve-out in the parenthetical check.
_PROSE_LABEL_VALUE = r"\*{0,2}\s*:\s*\*{0,2}\s*(\$[\d,]+(?:\.\d{2})?)"

_PROSE_FEE_RE = re.compile(
    r"(?i)\*{0,2}(?:Professional\s+(?:services\s+)?fees?|Total\s+agency\s+fees?)"
    + _PROSE_LABEL_VALUE
)
_PROSE_DIRECT_RE = re.compile(
    r"(?i)\*{0,2}(?:Direct\s+travel\s*/\s*reimbursables|Direct\s+travel|"
    r"Travel\s*/\s*reimbursables)" + _PROSE_LABEL_VALUE
)
_PROSE_TOTAL_RE = re.compile(
    r"(?i)\*{0,2}(?:Total\s+proposed\s+investment|Total\s+client\s+invoicing|"
    r"Grand\s+total)" + _PROSE_LABEL_VALUE
)
_PROSE_PASSTHROUGH_RE = re.compile(
    r"(?i)\*{0,2}Client\s+media\s+pass-?through[^:]*" + _PROSE_LABEL_VALUE
)


def _money(token: str) -> float:
    return float(token.replace("$", "").replace(",", ""))


def collect_prose_arithmetic_violations(markdown: str) -> list[str]:
    """Verify the rendered budget adds up, independently of the canonical object.

    The existing prose check compares each labelled figure against its own
    canonical field and never across fields, so fee == travel == total passed.
    """
    text = markdown or ""
    violations: list[str] = []

    fee_m = _PROSE_FEE_RE.search(text)
    total_m = _PROSE_TOTAL_RE.search(text)
    direct_m = _PROSE_DIRECT_RE.search(text)
    pt_m = _PROSE_PASSTHROUGH_RE.search(text)

    fee = _money(fee_m.group(1)) if fee_m else 0.0
    direct = _money(direct_m.group(1)) if direct_m else 0.0
    passthrough = _money(pt_m.group(1)) if pt_m else 0.0

    if fee_m and total_m:
        total = _money(total_m.group(1))
        expected = round(fee + direct + passthrough, 2)
        tol = max(1.0, total * 0.02)
        if abs(total - expected) > tol:
            violations.append(
                f"Budget prose does not add up: fees ${fee:,.0f} + direct ${direct:,.0f} "
                f"+ pass-through ${passthrough:,.0f} = ${expected:,.0f}, but the stated total "
                f"is ${total:,.0f}."
            )

    # A parenthetical that restates the whole total only omits something when the
    # total actually HAS more than one component. Fee-only, travel-only and
    # pass-through-only budgets are all shapes render_budget_markdown supports,
    # and for each _rewrite_investment_sentence correctly emits
    # "Total proposed investment: $X ($X in <the one component>)" — whole == part
    # with nothing left out. Flagging that never self-resolves, because
    # rerender_budget_section_from_canon reproduces identical text from the same
    # canonical budget every retry round, so it ends as a spurious manual-fill
    # handoff on a correct proposal. Zero parsed components is a different case:
    # every renderer path that emits a parenthetical also emits at least one
    # labelled component line, so prose with none is drifted text, not canon —
    # keep checking it.
    if len([c for c in (fee, direct, passthrough) if c > 0]) != 1:
        for m in re.finditer(
            r"(?i)Total[^.$]*(\$[\d,]+(?:\.\d{2})?)\s*\((\$[\d,]+(?:\.\d{2})?)[^)]*\)", text
        ):
            whole, part = _money(m.group(1)), _money(m.group(2))
            if abs(whole - part) < 0.01 and whole > 0:
                violations.append(
                    f"Budget total ${whole:,.0f} equals its own parenthetical breakdown — "
                    "the sentence claims the whole and a part are the same figure."
                )

    return violations


def _template_for_claim(claim_type: str, value: float) -> str | None:
    templates = {
        "agency_fee": f"Total Year 1 agency fee: {_usd(value)}.",
        "media_passthrough": f"Client media spend is billed at net cost ({_usd(value)}), separate from agency fees.",
        "direct_expenses": f"Direct travel/reimbursables: {_usd(value)}.",
        "total_invoicing": f"Total Year 1 client invoicing: {_usd(value)}.",
    }
    return templates.get(claim_type)


async def align_fee_narrative_with_budget(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> ProposalDraft:
    """Constrained slot-fill sync: replace dollar-claim sentences with approved templates."""
    sections = list(draft.sections)
    budget_idx = find_budget_section_index(sections)
    targets: list[tuple[int, ProposalSection]] = []
    for index, section in enumerate(sections):
        if _needs_fee_sync(section, budget_idx, index):
            targets.append((index, section))

    if not targets:
        return draft

    rfp = get_rfp(rfp_id)
    if not rfp:
        logger.warning("Fee sync skipped — RFP %s not found", rfp_id)
        return draft
    content = _assess_rfp_content(rfp)
    rfp_context = _build_rfp_context(rfp, content)
    canonical = _canonical_budget_facts(budget)
    slot_values = _canonical_slot_values(budget)
    updated_sections = list(sections)
    batch_size = 6
    for batch_start in range(0, len(targets), batch_size):
        batch = targets[batch_start : batch_start + batch_size]
        payload = [
            {
                "sectionId": section.id,
                "title": section.title,
                "content": section.content[:7000],
            }
            for _, section in batch
        ]
        try:
            raw, _provider = await llm.chat_json(
                [
                    {"role": "system", "content": FEE_SLOT_PLAN_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"RFP: {rfp.title}\nClient: {rfp.client}\n\n"
                            f"=== CANONICAL BUDGET ===\n{canonical}\n\n"
                            f"=== SECTIONS ===\n{payload}\n\n"
                            f"RFP fee excerpt:\n{rfp_context[:4000]}"
                        ),
                    },
                ],
                max_tokens=4096,
                temperature=0.0,
                node_name="fee_slot_fill_plan",
            )
        except LlmError as exc:
            logger.warning("Fee slot sync batch failed for %s: %s", rfp_id, exc)
            continue

        claims = [c for c in (raw.get("claims") or []) if isinstance(c, dict)]
        by_section: dict[str, list[dict]] = {}
        for claim in claims:
            sid = str(claim.get("sectionId") or "").strip()
            if not sid:
                continue
            by_section.setdefault(sid, []).append(claim)

        for index, section in batch:
            section_claims = by_section.get(section.id) or []
            if not section_claims:
                continue
            body = section.content or ""
            for claim in section_claims:
                sentence = str(claim.get("sentence") or "").strip()
                claim_type = str(claim.get("claimType") or "").strip()
                if not sentence or claim_type not in slot_values:
                    continue
                template = _template_for_claim(claim_type, slot_values[claim_type])
                if not template:
                    continue
                if sentence in body:
                    body = body.replace(sentence, template, 1)
            if body != (section.content or ""):
                updated_sections[index] = section.model_copy(update={"content": body})
                logger.info("Fee slot sync updated section %s (%s)", section.id, section.title)

    return draft.model_copy(update={"sections": updated_sections})


async def run_budget_grounding_check(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> list[BudgetNarrativeMismatch]:
    """Phase 3.5d: detect claims-vs-canonical budget mismatches across manuscript."""
    deterministic = collect_deterministic_budget_mismatches(draft, budget)

    budget_idx = find_budget_section_index(draft.sections)
    if budget_idx is not None:
        budget_section = draft.sections[budget_idx]
        for violation in collect_prose_arithmetic_violations(budget_section.content or ""):
            deterministic.append(
                BudgetNarrativeMismatch(
                    sectionId=budget_section.id,
                    sectionTitle=budget_section.title or "",
                    sentence=violation[:500],
                    claimedField="prose_arithmetic",
                    canonicalValue=None,
                    matches=False,
                    note=violation,
                )
            )

    sections = [
        {
            "sectionId": s.id,
            "sectionTitle": s.title,
            "content": (s.content or "")[:9000],
        }
        for s in draft.sections
        if s.content and _FEE_CONTENT_RE.search(s.content)
    ]
    if not sections:
        return deterministic

    canonical = _canonical_budget_facts(budget)
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": FEE_GROUNDING_CHECK_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"RFP ID: {rfp_id}\n\n"
                        f"=== CANONICAL BUDGET ===\n{canonical}\n\n"
                        f"=== MANUSCRIPT SECTIONS ===\n{sections}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.0,
            node_name="budget_claim_grounding_check",
        )
    except LlmError as exc:
        logger.warning(
            "Budget grounding LLM failed for %s — using deterministic mismatches only: %s",
            rfp_id,
            exc,
        )
        return deterministic

    out: list[BudgetNarrativeMismatch] = list(deterministic)
    seen = {
        (m.section_id, m.claimed_field, round(float(m.canonical_value or 0), 2), m.sentence[:80])
        for m in out
    }
    for row in (raw.get("mismatches") or []):
        if not isinstance(row, dict):
            continue
        try:
            item = BudgetNarrativeMismatch(**row)
        except Exception:
            continue
        if item.matches:
            continue
        key = (
            item.section_id,
            item.claimed_field,
            round(float(item.canonical_value or 0), 2),
            (item.sentence or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
