"""Opportunity shape + compensation classification and deterministic score caps.

Classification is LLM-judged with a verbatim RFP quote — no regex synonym tables.
Score caps apply only after classification; they never invent evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

OpportunityClass = Literal["professional_services", "open_competition", "ambiguous"]
CompensationSignal = Literal[
    "confirmed_fee",
    "prize_only",
    "explicitly_unpaid",
    "undisclosed",
]

_DIM_FINANCIAL = "financial viability"
_DIM_STRATEGIC = "strategic value"
_DIM_WIN = "win probability"

_OPPORTUNITY_CLASSIFIER_PROMPT = """You classify an RFP's opportunity shape and compensation model for a marketing agency Go/No-Go decision.

Read the RFP excerpt and return JSON only:
{
  "opportunityClass": "professional_services" | "open_competition" | "ambiguous",
  "compensationSignal": "confirmed_fee" | "undisclosed" | "explicitly_unpaid" | "prize_only",
  "evidenceQuote": "verbatim phrase from the RFP that supports compensationSignal (required)",
  "rationale": "one sentence"
}

Principles (judge by meaning — no keyword shortcuts):
- professional_services: agency delivers scoped marketing/branding/communications work under contract.
- open_competition: public design contest / open call where participants submit concepts for recognition or prize, not a professional fee engagement.
- confirmed_fee: RFP states paid contract mechanics — fixed ceiling/NTE, price agreement, IDIQ/task-order vehicle, hourly rate schedule, invoicing/NET terms, sample services agreement for paid work.
- undisclosed: paid professional-services shape but no reliable contract ceiling or rate table in the excerpt (common for IDIQ without a dollar cap stated upfront).
- explicitly_unpaid: deliverable work itself is unpaid, volunteer-only, or prize/recognition-only — NOT standard bid-prep boilerplate.
- CRITICAL: "No payment for proposal preparation" / "costs incurred prior to award" / "no fee for submitting a proposal" describe BID PREP only. That is NOT explicitly_unpaid when the RFP also requests hourly rates, price agreements, invoicing, or paid services scope.
- prize_only: compensation is honorarium/stipend/public recognition without a professional services contract.

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
    if opp_raw in {"professional_services", "open_competition", "ambiguous"}:
        opp_class = opp_raw  # type: ignore[assignment]
    else:
        opp_class = "ambiguous"

    comp: CompensationSignal
    if comp_raw in {"confirmed_fee", "undisclosed", "explicitly_unpaid", "prize_only"}:
        comp = comp_raw  # type: ignore[assignment]
    else:
        comp = "undisclosed"

    if quote and rfp_text and quote.casefold() not in rfp_text.casefold():
        # Ungrounded quote — do not trust explicitly_unpaid / prize_only caps.
        logger.warning(
            "opportunity classifier quote not found in RFP — downgrading to undisclosed "
            "(quote=%r)",
            quote[:80],
        )
        if comp in {"explicitly_unpaid", "prize_only"}:
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
                {"role": "system", "content": _OPPORTUNITY_CLASSIFIER_PROMPT},
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
