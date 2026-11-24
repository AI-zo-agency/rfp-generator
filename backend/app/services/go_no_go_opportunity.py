"""Opportunity shape + compensation classification and deterministic score caps.

Classification is LLM-judged with a verbatim RFP quote — no regex synonym tables.
Score caps apply only after classification; they never invent evidence.

Budget-band deal-breaker ($100k–$1M / year) is mechanical math on already-extracted
contract-value lines — not capability synonym matching.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal
from app.services.prompt_store import get_prompt

logger = logging.getLogger(__name__)

OpportunityClass = Literal[
    "professional_services",
    "open_competition",
    "funding_program",
    "ambiguous",
]
CompensationSignal = Literal[
    "confirmed_fee",
    "prize_only",
    "explicitly_unpaid",
    "undisclosed",
]

# zö pursuit band (Todd / leadership, Nov 2025 assessment): annual only.
BUDGET_BAND_MIN_ANNUAL = 100_000.0
BUDGET_BAND_MAX_ANNUAL = 1_000_000.0

_DIM_FINANCIAL = "financial viability"
_DIM_STRATEGIC = "strategic value"
_DIM_WIN = "win probability"

_ANNUAL_BUDGET_HINT_RE = re.compile(
    r"(?i)\b(?:"
    r"annual|per\s+year|/year|a\s+year|"
    r"year\s*(?:1|one)|yr\.?\s*1\b|first\s+year|"
    r"annually"
    r")\b"
)
_LINE_MONEY_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?",
    re.IGNORECASE,
)

_VALID_OPPORTUNITY_CLASSES = frozenset(
    {"professional_services", "open_competition", "funding_program", "ambiguous"}
)

_OPPORTUNITY_CLASSIFIER_PROMPT = """You classify an RFP's opportunity shape and compensation model for a marketing agency Go/No-Go decision.

Read the RFP excerpt and return JSON only:
{
  "opportunityClass": "professional_services" | "open_competition" | "funding_program" | "ambiguous",
  "compensationSignal": "confirmed_fee" | "undisclosed" | "explicitly_unpaid" | "prize_only",
  "evidenceQuote": "verbatim phrase from the RFP that supports compensationSignal (required)",
  "rationale": "one sentence"
}

Principles (judge by meaning — no keyword shortcuts):
- professional_services: buyer hires the firm under a paid vendor/services contract for scoped work.
- funding_program: applicant seeks an award from a funding pool to deliver a proposed project (grant or similar funded application). Award size is often applicant-defined or undisclosed. Seeking funding ≠ being hired on a vendor SOW. This is NOT open_competition.
- open_competition: ONLY contests / open calls where participants submit creative concepts for prize, honorarium, or recognition — not hired as a contractor and not applying for project funding.
- CRITICAL separation: if applicants compete for project funding (even when they invent the project/budget and present to a panel), classify funding_program — never open_competition or prize_only.
- confirmed_fee: paid contract mechanics — fixed ceiling/NTE, price agreement, IDIQ/task-order vehicle, hourly rates, invoicing/NET terms, sample services agreement. An applicant-requested funding ask is NOT confirmed_fee unless the notice awards a hired vendor under contract pay terms.
- undisclosed: no reliable contract ceiling or rate table (IDIQ without a stated cap; funding_program when award size is applicant-defined or unstated). Prefer undisclosed for funding_program unless the notice states a fixed prize/honorarium (then prize_only / open_competition may apply).
- explicitly_unpaid: the deliverable work itself is unpaid/volunteer — NOT bid-prep boilerplate, and NOT a funding award that pays for project delivery.
- CRITICAL: "No payment for proposal preparation" / "costs incurred prior to award" describe BID PREP only — NOT explicitly_unpaid when the solicitation also has paid contract mechanics.
- prize_only: honorarium/stipend/recognition without a professional-services contract and without project-funding award mechanics.

evidenceQuote MUST appear verbatim (or near-verbatim) in the RFP text provided."""


@dataclass(frozen=True)
class OpportunityClassification:
    opportunity_class: OpportunityClass
    compensation_signal: CompensationSignal
    evidence_quote: str = ""
    rationale: str = ""


def default_opportunity_classification() -> OpportunityClassification:
    """Safe fallback when LLM classification is unavailable."""
    return OpportunityClassification(
        opportunity_class="ambiguous",
        compensation_signal="undisclosed",
    )


def _normalize_for_quote_match(text: str) -> str:
    """Collapse whitespace so PDF newlines don't fail verbatim quote checks."""
    return " ".join((text or "").casefold().split())


def parse_opportunity_classification(
    raw: dict[str, Any],
    *,
    rfp_text: str,
) -> OpportunityClassification:
    """Parse LLM JSON and verify the quote is grounded in the RFP text."""
    opp_raw = str(
        raw.get("opportunityClass") or raw.get("opportunity_class") or "ambiguous"
    ).strip()
    comp_raw = str(
        raw.get("compensationSignal") or raw.get("compensation_signal") or "undisclosed"
    ).strip()
    quote = str(raw.get("evidenceQuote") or raw.get("evidence_quote") or "").strip()
    rationale = str(raw.get("rationale") or "").strip()

    opp_class: OpportunityClass
    if opp_raw in _VALID_OPPORTUNITY_CLASSES:
        opp_class = opp_raw  # type: ignore[assignment]
    else:
        opp_class = "ambiguous"

    comp: CompensationSignal
    if comp_raw in {"confirmed_fee", "undisclosed", "explicitly_unpaid", "prize_only"}:
        comp = comp_raw  # type: ignore[assignment]
    else:
        comp = "undisclosed"

    if quote and rfp_text:
        quote_ok = _normalize_for_quote_match(quote) in _normalize_for_quote_match(
            rfp_text
        )
        if not quote_ok:
            prior = comp
            if comp in {"explicitly_unpaid", "prize_only"}:
                comp = "undisclosed"
            logger.warning(
                "opportunity classifier quote not found in RFP%s (quote=%r)",
                (
                    " — downgrading unpaid/prize to undisclosed"
                    if prior != comp
                    else ""
                ),
                quote[:80],
            )

    # Safety: prize_only / explicitly_unpaid on a funding_program shape is almost
    # always a mislabel (grant award ≠ contest prize). Keep class; soften signal.
    if opp_class == "funding_program" and comp in {"explicitly_unpaid", "prize_only"}:
        logger.info(
            "opportunity classifier: funding_program + %s → undisclosed "
            "(grant award is not unpaid/prize contest pay)",
            comp,
        )
        comp = "undisclosed"

    return OpportunityClassification(
        opportunity_class=opp_class,
        compensation_signal=comp,
        evidence_quote=quote,
        rationale=rationale,
    )


async def classify_opportunity_llm(
    text: str,
    *,
    rfp_id: str = "",
    title: str = "",
) -> OpportunityClassification:
    """One light LLM call — opportunity shape + compensation with grounded quote."""
    body = (text or "").strip()
    if not body:
        return default_opportunity_classification()

    from app.services import llm

    if not llm.is_configured():
        return default_opportunity_classification()

    excerpt = body[:24_000]
    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": get_prompt("go_no_go_opportunity.opportunity_classifier", _OPPORTUNITY_CLASSIFIER_PROMPT)},
                {
                    "role": "user",
                    "content": (
                        f"RFP title: {title or '(not provided)'}\n\n"
                        f"RFP text:\n{excerpt}"
                    ),
                },
            ],
            max_tokens=512,
            temperature=0.0,
            tier="light",
            node_name="go_no_go_opportunity_classifier",
            rfp_id=rfp_id,
        )
        parsed = parse_opportunity_classification(
            raw if isinstance(raw, dict) else {},
            rfp_text=excerpt,
        )
        logger.info(
            "opportunity classifier for %s via %s: class=%s compensation=%s",
            rfp_id or "unknown",
            provider,
            parsed.opportunity_class,
            parsed.compensation_signal,
        )
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "opportunity classifier failed for %s: %s",
            rfp_id or "unknown",
            str(exc)[:160],
        )
        return default_opportunity_classification()


def has_confirmed_professional_fee(
    compensation_signal: CompensationSignal,
    *,
    contract_value_lines: list[str] | None = None,
) -> bool:
    if compensation_signal == "confirmed_fee":
        return True
    return bool(contract_value_lines)


@dataclass(frozen=True)
class BudgetBandAssessment:
    """Result of comparing extracted contract dollars to the pursuit band."""

    status: Literal["in_band", "below", "above", "unknown"]
    amount: float | None = None
    used_annual: bool = False
    evidence_line: str = ""


def assess_budget_band(
    contract_value_lines: list[str] | None,
) -> BudgetBandAssessment:
    """Compare HARD FACTS contract-value lines to the $100k–$1M / year band.

    Prefer annual / Year-1 figures. When only undifferentiated totals exist, apply
    the band only if every amount sits on the same side (all below or all above).
    Mixed annual-vs-total disagreement → unknown (LLM + leadership).
    """
    from app.services.evidence_trust.rfp_hard_facts import money_to_number

    lines = [str(x).strip() for x in (contract_value_lines or []) if str(x).strip()]
    if not lines:
        return BudgetBandAssessment(status="unknown")

    annual: list[tuple[float, str]] = []
    other: list[tuple[float, str]] = []
    for line in lines:
        for match in _LINE_MONEY_RE.finditer(line):
            value = money_to_number(match.group(1), match.group(2))
            if value is None or value <= 0:
                continue
            # Skip tiny form/bond dollars that sometimes share a ceiling window.
            if value < 1_000:
                continue
            bucket = annual if _ANNUAL_BUDGET_HINT_RE.search(line) else other
            bucket.append((value, line))

    if annual:
        amount, evidence = max(annual, key=lambda row: row[0])
        used_annual = True
    elif other:
        values = [v for v, _ in other]
        lo, hi = min(values), max(values)
        # Only force when every extracted total is on the same side of the band.
        if hi < BUDGET_BAND_MIN_ANNUAL:
            amount, evidence = max(other, key=lambda row: row[0])
            used_annual = False
        elif lo > BUDGET_BAND_MAX_ANNUAL:
            amount, evidence = max(other, key=lambda row: row[0])
            used_annual = False
        else:
            return BudgetBandAssessment(status="unknown")
    else:
        return BudgetBandAssessment(status="unknown")

    if amount < BUDGET_BAND_MIN_ANNUAL:
        status: Literal["in_band", "below", "above", "unknown"] = "below"
    elif amount > BUDGET_BAND_MAX_ANNUAL:
        status = "above"
    else:
        status = "in_band"
    return BudgetBandAssessment(
        status=status,
        amount=amount,
        used_annual=used_annual,
        evidence_line=evidence,
    )


def apply_budget_band_deal_breaker(
    raw: dict[str, Any],
    *,
    contract_value_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Force no_go when disclosed contract value is outside $100k–$1M / year."""
    if raw.get("insufficientData"):
        return raw

    assessment = assess_budget_band(contract_value_lines)
    if assessment.status not in {"below", "above"} or assessment.amount is None:
        return raw

    amount = assessment.amount
    period = "annual" if assessment.used_annual else "disclosed"
    if assessment.status == "below":
        message = (
            f"Contract value {period} ${amount:,.0f} is below zö's $100,000–$1,000,000 "
            "per-year pursuit band — deal-breaker unless leadership overrides."
        )
    else:
        message = (
            f"Contract value {period} ${amount:,.0f} is above zö's $100,000–$1,000,000 "
            "per-year pursuit band — deal-breaker unless leadership overrides."
        )

    _append_compliance_deal_breaker(raw, message)
    _append_gap(raw, message)
    _force_recommendation(raw, "no_go")
    _cap_worth(raw, 1)
    _cap_dimension(raw, _DIM_FINANCIAL, 1)
    _append_clarifying_question(
        raw,
        "Leadership override: pursue outside the $100k–$1M / year band? "
        f"(extracted {period} value ${amount:,.0f})",
    )
    return raw


def _append_compliance_deal_breaker(raw: dict[str, Any], message: str) -> None:
    compliance = raw.setdefault("compliance", {})
    if not isinstance(compliance, dict):
        compliance = {}
        raw["compliance"] = compliance
    flags = compliance.setdefault("flags", [])
    if not isinstance(flags, list):
        compliance["flags"] = []
        flags = compliance["flags"]
    needle = message[:64]
    if any(
        isinstance(f, dict)
        and f.get("category") == "deal_breaker"
        and needle in str(f.get("message") or "")
        for f in flags
    ):
        return
    flags.append(
        {
            "category": "deal_breaker",
            "severity": "critical",
            "message": message,
        }
    )


def apply_opportunity_score_caps(
    raw: dict[str, Any],
    *,
    opportunity_class: OpportunityClass,
    compensation_signal: CompensationSignal,
    contract_value_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Clamp matrix / worth scores. Never raises any score."""
    if raw.get("insufficientData"):
        return raw

    confirmed = has_confirmed_professional_fee(
        compensation_signal,
        contract_value_lines=contract_value_lines,
    )

    if opportunity_class == "open_competition" and not confirmed:
        _cap_worth(raw, 1)
        _cap_dimension(raw, _DIM_FINANCIAL, 0)
        _cap_dimension(raw, _DIM_STRATEGIC, 2)
        _cap_dimension(raw, _DIM_WIN, 2)
        _force_recommendation(raw, "no_go")
        _append_gap(
            raw,
            "Opportunity is an open design/community competition without a confirmed "
            "professional fee — speculative unpaid/prize work is not a paid services RFP.",
        )
    elif opportunity_class == "funding_program" and not confirmed:
        # Funding-award applications: unknown award size ≠ unpaid contest.
        # Soft caps only — never Financial 0 / forced no_go from shape alone.
        _cap_worth(raw, 3)
        _cap_dimension(raw, _DIM_FINANCIAL, 2)
        if raw.get("recommendation") == "go":
            raw["recommendation"] = "review"
        _append_gap(
            raw,
            "Funding-award program (not a paid vendor contract): confirm applicant "
            "eligibility (including whether a for-profit firm may be applicant of "
            "record or only a named partner/contractor) and realistic award economics "
            "before treating as Go. Undisclosed award size is not Financial Viability "
            "0 / automatic Pass.",
        )
        _append_clarifying_question(
            raw,
            "Who may be the applicant of record under this funding program, and can a "
            "for-profit firm apply directly or only as a named partner/contractor to an "
            "eligible applicant?",
        )
    elif opportunity_class == "ambiguous" and not confirmed:
        _cap_worth(raw, 2)
        _cap_dimension(raw, _DIM_FINANCIAL, 1)
        if raw.get("recommendation") == "go":
            raw["recommendation"] = "review"
        _append_gap(
            raw,
            "Opportunity type / compensation unclear — confirm paid professional-services "
            "engagement and eligibility before treating as Go.",
        )
    elif compensation_signal in {"explicitly_unpaid", "prize_only"} and not confirmed:
        _cap_worth(raw, 1)
        _cap_dimension(raw, _DIM_FINANCIAL, 0)
        _cap_dimension(raw, _DIM_STRATEGIC, 2)
        _cap_dimension(raw, _DIM_WIN, 2)
        _force_recommendation(raw, "no_go")
        _append_gap(
            raw,
            "Compensation is unpaid or prize-only — financial viability is not a paid "
            "agency engagement.",
        )

    return raw


def format_opportunity_facts_lines(
    opportunity_class: OpportunityClass,
    compensation_signal: CompensationSignal,
    *,
    evidence_quote: str = "",
    rationale: str = "",
) -> list[str]:
    lines = [
        f"- Opportunity class (LLM): {opportunity_class}",
        f"- Compensation signal (LLM): {compensation_signal}",
    ]
    if evidence_quote:
        lines.append(f'- Compensation evidence (verbatim): "{evidence_quote[:240]}"')
    if rationale:
        lines.append(f"- Classifier rationale: {rationale[:240]}")
    lines.extend(
        [
            "- If opportunity class is open_competition and compensation is not confirmed_fee: "
            "Financial Viability must be 0, Worth ≤ 1, Strategic ≤ 2, Win ≤ 2, prefer no_go. "
            "Do NOT treat this as a normal paid professional-services RFP.",
            "- If opportunity class is funding_program: Financial Viability ≤ 2 and Worth ≤ 3 "
            "when award size is undisclosed — NOT Financial 0 / automatic no_go. Prefer "
            "recommendation=review until applicant eligibility and award economics are "
            "confirmed. Never label funding_program as open_competition or unpaid/prize work.",
            "- If professional_services with undisclosed budget: Worth ~3 (mixed) is allowed; "
            "do NOT invent a fee, and do NOT force Financial to 0 solely for undisclosed budget.",
            '- "No payment for proposal preparation" is bid-prep boilerplate — NOT explicitly_unpaid '
            "when hourly rates / price agreement / invoicing appear elsewhere in the RFP.",
        ]
    )
    return lines


def _matrix_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = raw.get("decisionMatrix")
    if not isinstance(matrix, list):
        return []
    return [row for row in matrix if isinstance(row, dict)]


def _cap_dimension(raw: dict[str, Any], dimension_cf: str, max_score: int) -> None:
    for row in _matrix_rows(raw):
        dim = str(row.get("dimension") or "").casefold()
        if dim != dimension_cf:
            continue
        try:
            score = int(row.get("score"))
        except (TypeError, ValueError):
            continue
        if score > max_score:
            row["score"] = max_score
            note = str(row.get("notes") or "").strip()
            suffix = f"[capped to {max_score}/5: opportunity/compensation hard rule]"
            if suffix not in note:
                row["notes"] = f"{note} {suffix}".strip()


def _cap_worth(raw: dict[str, Any], max_score: int) -> None:
    value = raw.get("worthScore")
    if value is None:
        return
    try:
        score = int(value)
    except (TypeError, ValueError):
        return
    if score > max_score:
        raw["worthScore"] = max_score


def _force_recommendation(raw: dict[str, Any], value: str) -> None:
    current = raw.get("recommendation")
    if current is None:
        return
    raw["recommendation"] = value


def _append_gap(raw: dict[str, Any], message: str) -> None:
    gaps = raw.setdefault("criticalGaps", [])
    if not isinstance(gaps, list):
        return
    if any(isinstance(g, str) and message[:48] in g for g in gaps):
        return
    gaps.append(message)


def _append_clarifying_question(raw: dict[str, Any], question: str) -> None:
    qs = raw.setdefault("clarifyingQuestions", [])
    if not isinstance(qs, list):
        return
    needle = question[:48]
    if any(isinstance(q, str) and needle in q for q in qs):
        return
    qs.append(question)
