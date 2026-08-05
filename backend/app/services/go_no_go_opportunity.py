"""Classify opportunity shape + apply deterministic Go/No-Go score caps.

Stage 1 must distinguish paid professional-services RFPs from open design
competitions / unpaid speculative work before averaging matrix vibes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

OpportunityClass = Literal["professional_services", "open_competition", "ambiguous"]
CompensationSignal = Literal[
    "confirmed_fee",
    "prize_only",
    "explicitly_unpaid",
    "undisclosed",
]

_OPEN_COMPETITION_RES = (
    re.compile(r"\bcommunity\s+design\s+competition\b", re.I),
    re.compile(r"\bartists?,?\s+designers?,?\s+and\s+community\s+members\b", re.I),
    re.compile(
        r"\binvites?\s+(?:artists?|designers?|community\s+members?)\b",
        re.I,
    ),
    re.compile(
        r"\bsubmit\s+(?:your|an?\s+original)\s+(?:design|concept|seal|logo)\b",
        re.I,
    ),
    re.compile(r"\bdesign\s+competition\b", re.I),
    re.compile(r"\bopen\s+(?:call|competition)\s+for\s+(?:design|artists?)\b", re.I),
)

_PROFESSIONAL_SERVICES_RES = (
    re.compile(r"\bscope\s+of\s+services\b", re.I),
    re.compile(r"\bprofessional\s+services\s+agreement\b", re.I),
    re.compile(r"\brequest\s+for\s+proposals?\b", re.I),
    re.compile(
        r"\b(?:methodology|staffing\s+plan|cost\s+proposal|team\s+bios?|"
        r"case\s+studies|approach\s+and\s+methodology)\b",
        re.I,
    ),
    re.compile(
        r"\bqualified\s+(?:marketing\s+)?(?:agenc(?:y|ies)|firms?)\b",
        re.I,
    ),
)

_CONFIRMED_FEE_RES = (
    re.compile(
        r"\b(?:not\s+to\s+exceed|NTE|fixed[\s-]?price\s+ceiling|"
        r"maximum\s+(?:contract|compensation|budget)|"
        r"compensation\s+shall(?:\s+not)?|"
        r"contract\s+(?:value|ceiling|amount)|"
        r"total\s+(?:contract|project|award)\s+(?:value|amount|budget))\b",
        re.I,
    ),
    re.compile(
        r"\$\s*[\d,]+(?:\.\d+)?\s*(?:million|m)?\b.{0,40}\b(?:ceiling|NTE|budget|fee)\b",
        re.I,
    ),
)

_PRIZE_ONLY_RES = (
    re.compile(r"\b(?:prize|honorarium|stipend)\b", re.I),
    re.compile(r"\bpublic\s+recognition\s+only\b", re.I),
)

_EXPLICITLY_UNPAID_RES = (
    re.compile(r"\b(?:no|without)\s+(?:compensation|payment|fee|remuneration)\b", re.I),
    re.compile(r"\bunpaid\s+(?:work|contest|competition|submission)\b", re.I),
    re.compile(r"\bvolunte?er(?:ed)?\s+(?:basis|submission)\b", re.I),
)

_DIM_FINANCIAL = "financial viability"
_DIM_STRATEGIC = "strategic value"
_DIM_WIN = "win probability"


def classify_opportunity(text: str) -> tuple[OpportunityClass, CompensationSignal]:
    """Deterministic opportunity + compensation signals from RFP body."""
    body = text or ""
    competition_hits = sum(1 for rx in _OPEN_COMPETITION_RES if rx.search(body))
    services_hits = sum(1 for rx in _PROFESSIONAL_SERVICES_RES if rx.search(body))

    if competition_hits >= 1 and services_hits == 0:
        opp_class: OpportunityClass = "open_competition"
    elif competition_hits >= 2 and services_hits < competition_hits:
        # Strong contest language outweighs thin "RFP" boilerplate headers.
        opp_class = "open_competition"
    elif services_hits >= 1 and competition_hits == 0:
        opp_class = "professional_services"
    elif services_hits > competition_hits:
        opp_class = "professional_services"
    elif competition_hits > services_hits:
        opp_class = "open_competition"
    else:
        opp_class = "ambiguous"

    compensation = _classify_compensation(body)
    return opp_class, compensation


def _classify_compensation(body: str) -> CompensationSignal:
    if any(rx.search(body) for rx in _EXPLICITLY_UNPAID_RES):
        return "explicitly_unpaid"
    if any(rx.search(body) for rx in _CONFIRMED_FEE_RES):
        return "confirmed_fee"
    if any(rx.search(body) for rx in _PRIZE_ONLY_RES):
        return "prize_only"
    return "undisclosed"


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
) -> list[str]:
    return [
        f"- Opportunity class (extracted): {opportunity_class}",
        f"- Compensation signal (extracted): {compensation_signal}",
        "- If opportunity class is open_competition and compensation is not confirmed_fee: "
        "Financial Viability must be 0, Worth ≤ 1, Strategic ≤ 2, Win ≤ 2, prefer no_go. "
        "Do NOT treat this as a normal paid professional-services RFP.",
        "- If professional_services with undisclosed budget: Worth ~3 (mixed) is allowed; "
        "do NOT invent a fee, and do NOT force Financial to 0 solely for undisclosed budget.",
    ]


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
