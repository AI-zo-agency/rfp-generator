"""Extract and enforce RFP money authority (hard NTE vs program/media envelopes).

RFP-agnostic: linguistic patterns only — never hardcode client names or fixed dollars.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.models.proposal import BudgetNarrativeMismatch, ProposalBudget
from app.services.evidence_trust.rfp_hard_facts import (
    _ELIGIBILITY_DOLLAR_CONTEXT_RE,
    _MONEY_RE,
    money_to_number,
)

logger = logging.getLogger(__name__)

CONSTRAINT_HARD_FEE_NTE = "hard_fee_nte"
CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE = "program_or_media_envelope"
CONSTRAINT_MINIMUM_BUDGET = "minimum_budget"

# --- RFP-stated minimum budget -------------------------------------------
# The buyer telling you the floor of what they expect to spend. Distinct from
# proposal_budget_floor.py, which is zo's OWN 00_Guide_Pricing rate floor.
# Incident: a $500,000 "minimum budgeted amount" had nowhere to land, so a
# $278,400 bid passed every gate and left ~$220k on the table.

_MINIMUM_LANGUAGE_RE = re.compile(
    r"(?:minimum|at\s+least|no\s+less\s+than|not\s+less\s+than|no\s+lower\s+than|"
    r"floor\s+of|starting\s+at)",
    re.I,
)

# A minimum only counts when it is a minimum OF THE ENGAGEMENT'S MONEY.
_BUDGET_TOPIC_RE = re.compile(
    r"(?:budget(?:ed|ing)?|compensation|professional\s+fees?|"
    r"contract\s+(?:value|amount|award|price)|award\s+amount|"
    r"project\s+(?:budget|value|cost|amount)|"
    r"(?:cost|fee|price)\s+proposal|"
    r"total\s+(?:project|contract|proposed)\s+(?:cost|price|amount|value)|"
    r"estimated\s+(?:cost|value|spend)|anticipated\s+(?:budget|value|award|spend))",
    re.I,
)

# "minimum" in an RFP is overwhelmingly an insurance / bonding / eligibility
# word. A false floor is worse than a missed one — it drives the repair loop to
# inflate a correctly-priced bid — so every one of these vetoes the match.
_MINIMUM_BUDGET_EXCLUSION_RE = re.compile(
    r"(?:insurance|liabilit|coverage|indemnit|\bbond(?:s|ed|ing)?\b|surety|"
    r"workers?'?\s+comp|umbrella|aggregate|per\s+occurrence|deductible|"
    r"self[-\s]insured\s+retention|"
    r"annual\s+(?:gross\s+)?(?:revenue|receipts|sales)|net\s+worth|"
    r"bid\s+(?:security|guarantee)|liquidated\s+damages|penalt|"
    r"prevailing\s+wage|payroll|salar)",
    re.I,
)

# Explicit ceiling wording. Used to break the tie when a window carries BOTH
# minimum and maximum language — e.g. "minimum contract value", where
# "contract value" alone sits inside _HARD_FEE_CONTEXT_RE and would otherwise
# make one dollar figure serve as both the floor and the ceiling.
_EXPLICIT_MAXIMUM_RE = re.compile(
    r"(?:not\s+to\s+exceed|\bNTE\b|shall\s+not\s+exceed|cannot\s+exceed|"
    r"must\s+not\s+exceed|may\s+not\s+exceed|maximum|ceiling|"
    r"up\s+to|no\s+more\s+than|\bcap(?:ped)?\b)",
    re.I,
)

# "$500,000 to $750,000" / "between $250,000 and $400,000" — the low end is the
# stated floor. Requires a budget topic in the window (below) to fire.
_BUDGET_RANGE_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?"
    r"\s*(?:to|through|and|-|\u2013|\u2014)\s*"
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?",
    re.I,
)

_HARD_FEE_CONTEXT_RE = re.compile(
    r"(?:fixed[\s-]?price|not\s+to\s+exceed|NTE|"
    r"do\s+not\s+(?:exceed|go\s+above)|shall\s+not\s+exceed|cannot\s+exceed|"
    r"must\s+not\s+exceed|may\s+not\s+exceed|"
    r"maximum\s+(?:contract|compensation|budget|fee|available)|"
    r"available\s+funds|"
    r"total\s+(?:contract|project|award|available)\s+(?:value|amount|budget)|"
    r"contract\s+value|compensation\s+shall\s+not|"
    r"budget\s+(?:of|is|shall|not\s+to\s+exceed)\b|"
    r"year\s*(?:1|one)\s+budget|"
    r"ceiling\s+of|price\s+ceiling)",
    re.I,
)

_YEAR1_CONTEXT_RE = re.compile(
    r"year\s*(?:1|one)|first\s+year|initial\s+(?:term|year)|yr\.?\s*1\b",
    re.I,
)
_LATER_YEAR_CONTEXT_RE = re.compile(
    r"year\s*(?:2|3|two|three)|yr\.?\s*[23]\b|option\s+year|years?\s*2\s*[–\-]\s*3",
    re.I,
)

_PROGRAM_MEDIA_ENVELOPE_RE = re.compile(
    r"(?:allocat(?:ing|es|ed)|budget(?:ed|ing)?|funding|set\s+aside|earmark(?:ed|ing)?)"
    r".{0,80}?"
    r"(?:up\s+to|not\s+to\s+exceed|NTE|maximum\s+of|no\s+more\s+than)"
    r".{0,40}?"
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?",
    re.I | re.DOTALL,
)

_PROGRAM_MEDIA_TOPIC_RE = re.compile(
    r"(?:digital\s+advertis|paid\s+media|media\s+spend|program[- ]specific|"
    r"advertising\s+(?:budget|spend|allocation)|media\s+(?:budget|buy))",
    re.I,
)

# Alternate order: $X … for … advertising
_DOLLAR_THEN_MEDIA_TOPIC_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?"
    r".{0,100}?"
    r"(?:for|toward|towards|on)\s+"
    r"(?:program[- ]specific\s+)?(?:digital\s+)?(?:advertis\w+|paid\s+media|media\s+spend)",
    re.I | re.DOTALL,
)

_CEILING_CLAIM_RE = re.compile(
    r"(?:sits?\s+within|within|under|at\s+or\s+under|below|does\s+not\s+exceed|"
    r"respects?|matches?)\s+"
    r"(?:the\s+)?"
    r"(?:client'?s?|buyer'?s?|university'?s?|agency'?s?|RFP'?s?|[A-Z][\w .&-]{2,40}'?s?)?\s*"
    r"(?:\$\s*[\d,]+(?:\.\d+)?\s*(?:million|billion|m|b|k|thousand)?\s+)?"
    r"(?:ceiling|cap|allocation|envelope|maximum|NTE|budget)",
    re.I,
)

_USD_AMOUNT_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?",
    re.I,
)


@dataclass(frozen=True)
class RfpMoneyConstraint:
    amount: float
    kind: str
    excerpt: str
    confidence: str = "high"

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "kind": self.kind,
            "excerpt": self.excerpt,
            "confidence": self.confidence,
        }


def _window_excerpt(body: str, start: int, end: int, *, pad: int = 80) -> str:
    a = max(0, start - pad)
    b = min(len(body), end + pad)
    snippet = re.sub(r"\s+", " ", body[a:b]).strip()
    if len(snippet) > 200:
        snippet = snippet[:197] + "…"
    return snippet


def _parse_money_groups(amount: str, suffix: str | None) -> float | None:
    return money_to_number(amount, suffix)


def _nearest_year_kind(text: str, amount_start: int, amount_end: int) -> str:
    """Bind a dollar span to the closest year marker ('year1' | 'later' | '').

    Wide context windows often contain both Year 1 and Years 2–3. The nearest
    marker wins so a later-year figure cannot become the Year 1 bid ceiling.
    """
    window_start = max(0, amount_start - 80)
    window_end = min(len(text), amount_end + 40)
    snippet = text[window_start:window_end]
    rel_start = amount_start - window_start
    rel_end = amount_end - window_start

    def _dist(match: re.Match[str]) -> int:
        if match.end() <= rel_start:
            return rel_start - match.end()
        if match.start() >= rel_end:
            return match.start() - rel_end
        return 0

    y1 = [_dist(m) for m in _YEAR1_CONTEXT_RE.finditer(snippet)]
    later = [_dist(m) for m in _LATER_YEAR_CONTEXT_RE.finditer(snippet)]
    best_y1 = min(y1) if y1 else None
    best_later = min(later) if later else None
    if best_later is not None and (best_y1 is None or best_later < best_y1):
        return "later"
    if best_y1 is not None:
        return "year1"
    return ""


def extract_rfp_money_constraints(text: str) -> list[RfpMoneyConstraint]:
    """Deterministic RFP money authority candidates from full RFP body."""
    body = text or ""
    if not body.strip():
        return []

    found: list[RfpMoneyConstraint] = []
    seen: set[tuple[str, float]] = set()

    def _add(amount: float, kind: str, excerpt: str, confidence: str = "high") -> None:
        if amount <= 0:
            return
        key = (kind, round(amount, 2))
        if key in seen:
            return
        seen.add(key)
        found.append(
            RfpMoneyConstraint(
                amount=round(amount, 2),
                kind=kind,
                excerpt=excerpt,
                confidence=confidence,
            )
        )

    for match in _PROGRAM_MEDIA_ENVELOPE_RE.finditer(body):
        start, end = match.start(), match.end()
        window = body[max(0, start - 40) : min(len(body), end + 120)]
        if _ELIGIBILITY_DOLLAR_CONTEXT_RE.search(window):
            continue
        if not _PROGRAM_MEDIA_TOPIC_RE.search(window) and not _PROGRAM_MEDIA_TOPIC_RE.search(
            body[max(0, start - 160) : min(len(body), end + 160)]
        ):
            # "allocating up to $X" without media/advertising topic → skip here
            # (hard NTE path may still catch it).
            continue
        amount = _parse_money_groups(match.group(1), match.group(2))
        if amount is None:
            continue
        _add(
            amount,
            CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE,
            _window_excerpt(body, start, end),
        )

    for match in _DOLLAR_THEN_MEDIA_TOPIC_RE.finditer(body):
        start, end = match.start(), match.end()
        window = body[max(0, start - 40) : min(len(body), end + 40)]
        if _ELIGIBILITY_DOLLAR_CONTEXT_RE.search(window):
            continue
        amount = _parse_money_groups(match.group(1), match.group(2))
        if amount is None:
            continue
        _add(
            amount,
            CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE,
            _window_excerpt(body, start, end),
        )

    for match in _MONEY_RE.finditer(body):
        amount = _parse_money_groups(match.group(1), match.group(2))
        if amount is None:
            continue
        start, end = match.start(), match.end()
        window = body[max(0, start - 120) : min(len(body), end + 80)]
        if _ELIGIBILITY_DOLLAR_CONTEXT_RE.search(window):
            continue
        if not _HARD_FEE_CONTEXT_RE.search(window):
            continue
        # A stated MINIMUM is not a ceiling. Several _HARD_FEE_CONTEXT_RE
        # alternatives are ambiguous on their own ("contract value", "budget
        # of"), so "the minimum contract value is $500,000" matched here and
        # the one figure became both floor and cap. Explicit ceiling wording
        # anywhere in the window still wins.
        if _MINIMUM_LANGUAGE_RE.search(window) and not _EXPLICIT_MAXIMUM_RE.search(window):
            continue
        # Year 2/3 option dollars are not the Year 1 bid ceiling.
        if _nearest_year_kind(body, start, end) == "later":
            continue
        # Prefer not double-counting media envelopes as hard NTE when topic is media.
        if _PROGRAM_MEDIA_TOPIC_RE.search(window):
            _add(
                amount,
                CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE,
                _window_excerpt(body, start, end),
                confidence="medium",
            )
            continue
        _add(amount, CONSTRAINT_HARD_FEE_NTE, _window_excerpt(body, start, end))

    from app.services.evidence_trust.rfp_hard_facts import _YEAR_BUDGET_RE

    for match in _YEAR_BUDGET_RE.finditer(body):
        snippet = match.group(0)
        if not _YEAR1_CONTEXT_RE.search(snippet):
            continue
        if _LATER_YEAR_CONTEXT_RE.search(snippet):
            continue
        amount = _parse_money_groups(match.group(1), None)
        if amount is None:
            continue
        start, end = match.start(), match.end()
        window = body[max(0, start - 40) : min(len(body), end + 40)]
        if _ELIGIBILITY_DOLLAR_CONTEXT_RE.search(window):
            continue
        _add(
            amount,
            CONSTRAINT_HARD_FEE_NTE,
            _window_excerpt(body, start, end),
        )

    def _minimum_blocked(window: str) -> bool:
        return bool(
            _ELIGIBILITY_DOLLAR_CONTEXT_RE.search(window)
            or _MINIMUM_BUDGET_EXCLUSION_RE.search(window)
        )

    # Stated budget RANGE — the low end is the floor. No minimum wording is
    # needed ("budget of $500,000 to $750,000"), so this pass leans entirely on
    # the budget topic to stay off unrelated dollar pairs.
    for match in _BUDGET_RANGE_RE.finditer(body):
        low = _parse_money_groups(match.group(1), match.group(2))
        high = _parse_money_groups(match.group(3), match.group(4))
        if low is None or high is None or low >= high:
            continue
        start, end = match.start(), match.end()
        window = body[max(0, start - 140) : min(len(body), end + 60)]
        if _minimum_blocked(window) or not _BUDGET_TOPIC_RE.search(window):
            continue
        if _nearest_year_kind(body, start, end) == "later":
            continue
        _add(low, CONSTRAINT_MINIMUM_BUDGET, _window_excerpt(body, start, end))

    # Scalar stated minimum — requires minimum wording AND a budget topic.
    for match in _MONEY_RE.finditer(body):
        amount = _parse_money_groups(match.group(1), match.group(2))
        if amount is None:
            continue
        start, end = match.start(), match.end()
        window = body[max(0, start - 140) : min(len(body), end + 100)]
        if _minimum_blocked(window):
            continue
        if not _MINIMUM_LANGUAGE_RE.search(window):
            continue
        if not _BUDGET_TOPIC_RE.search(window):
            continue
        if _nearest_year_kind(body, start, end) == "later":
            continue
        _add(amount, CONSTRAINT_MINIMUM_BUDGET, _window_excerpt(body, start, end))

    logger.info(
        "rfp_money_constraints extracted count=%s kinds=%s",
        len(found),
        sorted({c.kind for c in found}),
    )
    return found


_NTE_FALLBACK_PROMPT = (
    "You extract ONE fact from an RFP: the maximum dollar figure the buyer "
    "states a bidder's fee/compensation must not exceed (a hard NTE / price "
    "ceiling / maximum contract value for the base or Year 1 term). This is "
    "a last-resort fallback after a deterministic regex pass found nothing — "
    "the RFP likely phrases its ceiling in wording the regex patterns don't "
    "anticipate, so read for MEANING, not just the keywords below.\n"
    "Rules:\n"
    "- Only report a figure the RFP text ACTUALLY STATES. Never estimate, "
    "infer from scope, or invent a number.\n"
    "- 'excerpt' MUST be copied VERBATIM from the RFP text (same characters, "
    "not paraphrased) — a short window containing the dollar figure and its "
    "context. This is checked programmatically; a paraphrase is rejected.\n"
    "- If the RFP states no such ceiling, return null — do not guess.\n"
    "- A Year-2/3 option-year figure or a total multi-year contract ceiling "
    "is not the Year 1 / base-term NTE — only report the base-term figure.\n"
    'Return JSON: {"amount": <number or null>, "excerpt": "<verbatim quote or null>"}'
)


async def llm_extract_hard_fee_nte(rfp_text: str) -> RfpMoneyConstraint | None:
    """LLM fallback for a hard fee NTE when the deterministic regex pass
    (extract_rfp_money_constraints) finds nothing — that pass is intentionally
    RFP-agnostic pattern matching, so it misses ceilings phrased in wording
    the patterns don't anticipate. Grounded: the returned excerpt must appear
    verbatim in the RFP text, or the result is rejected outright — this
    cannot invent a number, only fail to find one that is genuinely there.
    """
    body = (rfp_text or "").strip()
    if not body:
        return None

    from app.services import llm

    if not llm.is_configured():
        return None

    try:
        raw, _ = await asyncio.wait_for(
            llm.chat_json(
                [
                    {"role": "system", "content": _NTE_FALLBACK_PROMPT},
                    {"role": "user", "content": f"RFP text:\n{body[:60_000]}"},
                ],
                max_tokens=512,
                temperature=0.0,
                tier="light",
                node_name="rfp_money_nte_fallback",
            ),
            timeout=90.0,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort fallback, never blocks budget generation
        logger.warning("LLM hard-fee-NTE fallback failed: %s", exc)
        return None

    amount_raw = raw.get("amount") if isinstance(raw, dict) else None
    excerpt = str((raw or {}).get("excerpt") or "").strip()
    if amount_raw is None or not excerpt:
        return None
    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None

    # Grounding check: reject anything not actually present in the RFP text
    # (normalize whitespace only — never accept a paraphrase or invented quote).
    normalized_body = re.sub(r"\s+", " ", body).casefold()
    normalized_excerpt = re.sub(r"\s+", " ", excerpt).casefold()
    if normalized_excerpt not in normalized_body:
        logger.warning(
            "LLM hard-fee-NTE fallback rejected — excerpt not found verbatim in RFP: %r",
            excerpt[:120],
        )
        return None

    return RfpMoneyConstraint(
        amount=round(amount, 2),
        kind=CONSTRAINT_HARD_FEE_NTE,
        excerpt=excerpt[:200],
        confidence="medium",
    )


async def extract_rfp_money_constraints_with_llm_fallback(
    text: str,
) -> list[RfpMoneyConstraint]:
    """extract_rfp_money_constraints, plus a grounded LLM fallback for the
    hard fee NTE specifically when the deterministic pass finds none — the
    common case that leaves rfp_budget_cap unset and the over-cap check
    unable to run at all (see collect_over_authority_flags)."""
    constraints = extract_rfp_money_constraints(text)
    if any(c.kind == CONSTRAINT_HARD_FEE_NTE for c in constraints):
        return constraints
    fallback = await llm_extract_hard_fee_nte(text)
    if fallback is not None:
        constraints = [*constraints, fallback]
        logger.info(
            "rfp_money_constraints LLM fallback recovered hard_fee_nte=%s", fallback.amount
        )
    return constraints


def primary_hard_fee_nte(
    constraints: list[RfpMoneyConstraint],
) -> RfpMoneyConstraint | None:
    hard = [c for c in constraints if c.kind == CONSTRAINT_HARD_FEE_NTE]
    if not hard:
        return None

    def _kind(constraint: RfpMoneyConstraint) -> str:
        excerpt = constraint.excerpt or ""
        # Excerpts are short; locate the dollar then bind to the nearest year marker.
        money = _USD_AMOUNT_RE.search(excerpt)
        if money:
            return _nearest_year_kind(excerpt, money.start(), money.end())
        y1 = bool(_YEAR1_CONTEXT_RE.search(excerpt))
        later = bool(_LATER_YEAR_CONTEXT_RE.search(excerpt))
        if later and not y1:
            return "later"
        if y1 and not later:
            return "year1"
        return ""

    year1 = [c for c in hard if _kind(c) == "year1"]
    if year1:
        return min(year1, key=lambda c: c.amount)
    unyeared = [c for c in hard if _kind(c) != "later"]
    if unyeared:
        return min(unyeared, key=lambda c: c.amount)
    return min(hard, key=lambda c: c.amount)


def primary_program_media_envelope(
    constraints: list[RfpMoneyConstraint],
) -> RfpMoneyConstraint | None:
    env = [c for c in constraints if c.kind == CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE]
    if not env:
        return None
    return min(env, key=lambda c: c.amount)


def primary_minimum_budget(
    constraints: list[RfpMoneyConstraint],
) -> RfpMoneyConstraint | None:
    """Lowest extracted floor wins.

    Same reasoning as the ceiling picks, mirrored: taking the max would let one
    over-eager match force a correctly-priced bid upward, and this figure feeds
    a repair loop that rewrites the budget.
    """
    mins = [c for c in constraints if c.kind == CONSTRAINT_MINIMUM_BUDGET]
    if not mins:
        return None
    return min(mins, key=lambda c: c.amount)


def format_money_constraints_block(constraints: list[RfpMoneyConstraint]) -> str:
    if not constraints:
        return (
            "### RFP money constraints\n"
            "- No hard fee NTE or program/media envelope extracted from RFP body."
        )
    lines = ["### RFP money constraints (deterministic — cite; do not invent)"]
    for c in constraints:
        if c.kind == CONSTRAINT_HARD_FEE_NTE:
            label = "Hard fee / compensation NTE"
        elif c.kind == CONSTRAINT_MINIMUM_BUDGET:
            label = "RFP-stated MINIMUM budget (bid at or above this)"
        else:
            label = "Program / media spend envelope"
        lines.append(f"- {label}: ${c.amount:,.2f} — {c.excerpt}")
    lines.append(
        "- Never label the proposal's own bid total as the RFP ceiling/allocation "
        "unless that dollar equals an extracted constraint above."
    )
    return "\n".join(lines)


def apply_constraints_to_budget_fields(
    budget: ProposalBudget,
    constraints: list[RfpMoneyConstraint],
) -> ProposalBudget:
    """Merge extracted constraints onto budget fields (deterministic wins over empty)."""
    updates: dict[str, Any] = {}
    notes_parts: list[str] = []
    if (budget.rfp_money_constraint_notes or "").strip():
        notes_parts.append(budget.rfp_money_constraint_notes.strip())

    nte = primary_hard_fee_nte(constraints)
    if nte is not None:
        # Only overwrite null/zero, or replace if LLM invented a cap matching its own total.
        existing = budget.rfp_budget_cap
        own_total = float(
            budget.agency_revenue_estimate
            or budget.agency_fee_subtotal
            or budget.lump_sum_total
            or 0
        )
        invented = (
            existing is not None
            and own_total > 0
            and abs(float(existing) - own_total) < 1.0
            and abs(float(existing) - nte.amount) > 1.0
        )
        if existing is None or float(existing) <= 0 or invented:
            updates["rfp_budget_cap"] = nte.amount
            notes_parts.append(f"hard_fee_nte={nte.amount:,.2f}: {nte.excerpt}")
            if invented:
                logger.warning(
                    "Cleared invented rfpBudgetCap=%s matching own total; set NTE=%s",
                    existing,
                    nte.amount,
                )

    floor = primary_minimum_budget(constraints)
    if floor is not None:
        updates["rfp_budget_floor"] = floor.amount
        notes_parts.append(f"minimum_budget={floor.amount:,.2f}: {floor.excerpt}")

    envelope = primary_program_media_envelope(constraints)
    if envelope is not None:
        updates["rfp_media_or_program_envelope"] = envelope.amount
        notes_parts.append(
            f"program_or_media_envelope={envelope.amount:,.2f}: {envelope.excerpt}"
        )

    if notes_parts:
        # Dedupe while preserving order
        seen_n: set[str] = set()
        uniq: list[str] = []
        for part in notes_parts:
            key = part.casefold()
            if key in seen_n:
                continue
            seen_n.add(key)
            uniq.append(part)
        updates["rfp_money_constraint_notes"] = "\n".join(uniq)[:4000]

    if not updates:
        return budget
    return budget.model_copy(update=updates)


# A stated floor is a budget signal, not an arithmetic identity. Only a
# materially short bid is worth rewriting; a rounding gap is not.
MINIMUM_BUDGET_TOLERANCE = 0.99


def budget_total_for_minimum_check(budget: ProposalBudget) -> float:
    """The dollar the buyer compares against their stated minimum.

    Their floor describes what they expect the engagement to cost them, so the
    richest available client-facing total wins — a fee-only figure would read as
    a shortfall on any engagement whose media pass-through carries real dollars.
    """
    candidates = [
        budget.total_client_invoicing,
        budget.lump_sum_total,
        budget.agency_revenue_estimate,
        budget.agency_fee_subtotal,
        budget.line_item_sum,
    ]
    return max((float(c) for c in candidates if c is not None), default=0.0)


def collect_under_minimum_flags(budget: ProposalBudget) -> list[str]:
    """Pricing flags when the bid falls under an RFP-stated minimum budget."""
    floor = budget.rfp_budget_floor
    if floor is None or float(floor) <= 0:
        return []
    total = budget_total_for_minimum_check(budget)
    if total <= 0:
        return []
    if total >= float(floor) * MINIMUM_BUDGET_TOLERANCE:
        return []
    shortfall = float(floor) - total
    return [
        f"[PRICING FLAG: UNDERBID — proposed total ${total:,.2f} is below the "
        f"RFP-stated minimum budget ${float(floor):,.2f} (${shortfall:,.2f} left "
        f"on the table). Scope up to the stated minimum or confirm with Sonja]"
    ]


def collect_over_authority_flags(budget: ProposalBudget) -> list[str]:
    """Pricing flags when ledger exceeds extracted RFP authority."""
    flags: list[str] = []
    hard = budget.rfp_budget_cap
    agency = float(budget.agency_revenue_estimate or budget.agency_fee_subtotal or 0)
    if hard is not None and float(hard) > 0 and agency > float(hard) + 0.01:
        flags.append(
            f"[PRICING FLAG: DISQUALIFY RISK — agency revenue ${agency:,.2f} exceeds "
            f"RFP hard fee NTE ${float(hard):,.2f} — scope down or confirm with Sonja]"
        )

    envelope = budget.rfp_media_or_program_envelope
    invoicing = float(
        budget.total_client_invoicing
        or (
            float(budget.agency_fee_subtotal or agency)
            + float(budget.client_media_passthrough or 0)
            + float(budget.direct_expenses_total or 0)
        )
    )
    if envelope is not None and float(envelope) > 0 and invoicing > float(envelope) + 0.01:
        flags.append(
            f"[PRICING FLAG: DISQUALIFY RISK — total client invoicing ${invoicing:,.2f} "
            f"exceeds RFP program/media envelope ${float(envelope):,.2f} "
            f"(fee + media vs stated allocation) — Sonja confirm hard vs soft ceiling]"
        )
    return flags


def _amounts_near(value: float, candidates: set[float], *, tol: float = 1.0) -> bool:
    return any(abs(value - c) <= tol for c in candidates)


def collect_invented_ceiling_mismatches(
    content: str,
    *,
    budget: ProposalBudget,
    section_id: str,
    section_title: str,
) -> list[BudgetNarrativeMismatch]:
    """Flag prose that labels the bid total as the RFP ceiling/allocation."""
    body = content or ""
    if not body.strip():
        return []

    allowed_rfp: set[float] = set()
    if budget.rfp_budget_cap is not None and float(budget.rfp_budget_cap) > 0:
        allowed_rfp.add(round(float(budget.rfp_budget_cap), 2))
    if (
        budget.rfp_media_or_program_envelope is not None
        and float(budget.rfp_media_or_program_envelope) > 0
    ):
        allowed_rfp.add(round(float(budget.rfp_media_or_program_envelope), 2))

    bid_amounts: set[float] = set()
    for raw in (
        budget.agency_revenue_estimate,
        budget.agency_fee_subtotal,
        budget.total_client_invoicing,
        budget.lump_sum_total,
        budget.line_item_sum,
    ):
        if raw is not None and float(raw) > 0:
            bid_amounts.add(round(float(raw), 2))

    if not bid_amounts:
        return []

    out: list[BudgetNarrativeMismatch] = []
    # Sentence-ish splits
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        if not sentence.strip():
            continue
        if not _CEILING_CLAIM_RE.search(sentence) and not re.search(
            r"\b(?:ceiling|allocation|envelope)\b", sentence, re.I
        ):
            continue
        # Require ceiling/allocation language near a dollar
        if not re.search(r"\b(?:ceiling|cap|allocation|envelope|NTE)\b", sentence, re.I):
            continue
        for m in _USD_AMOUNT_RE.finditer(sentence):
            amount = _parse_money_groups(m.group(1), m.group(2))
            if amount is None:
                continue
            amount = round(amount, 2)
            if not _amounts_near(amount, bid_amounts):
                continue
            if allowed_rfp and _amounts_near(amount, allowed_rfp):
                continue
            # Bid dollar labeled as RFP ceiling but not an extracted RFP authority
            out.append(
                BudgetNarrativeMismatch(
                    sectionId=section_id,
                    sectionTitle=section_title,
                    sentence=sentence.strip()[:500],
                    claimedField="rfp_ceiling_claim",
                    canonicalValue=float(next(iter(allowed_rfp), 0.0)),
                    matches=False,
                    note=(
                        f"Manuscript labels bid amount ${amount:,.2f} as RFP "
                        f"ceiling/allocation, but that figure is not an extracted "
                        f"RFP money constraint"
                        + (
                            f" (known: {sorted(allowed_rfp)})"
                            if allowed_rfp
                            else " (none extracted)"
                        )
                    ),
                )
            )
            break
    return out


def constraints_prompt_block(constraints: list[RfpMoneyConstraint]) -> str:
    return format_money_constraints_block(constraints)
