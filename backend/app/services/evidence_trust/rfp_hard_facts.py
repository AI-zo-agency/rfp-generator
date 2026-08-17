"""Extract contract ceiling + evaluation weights from full RFP text.

Shared by Go/No-Go and proposal drafting so dollar tables survive chunking.
"""

from __future__ import annotations

import re
from typing import Any

_MONEY_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m|b|k|thousand)?",
    re.IGNORECASE,
)
_YEAR_BUDGET_RE = re.compile(
    r"(?:year\s*(?:1|2|3|one|two|three)|yr\.?\s*[123])"
    r".{0,80}?"
    r"\$\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)
_CEILING_CONTEXT_RE = re.compile(
    r"(?:fixed[\s-]?price|not\s+to\s+exceed|NTE|ceiling|maximum\s+(?:contract|compensation|budget)|"
    r"total\s+(?:contract|project|award)\s+(?:value|amount|budget)|contract\s+value|"
    r"compensation\s+shall\s+not|budget\s+(?:of|is|shall))",
    re.IGNORECASE,
)
# Small-business / vendor-eligibility dollars are NOT contract value.
_ELIGIBILITY_DOLLAR_CONTEXT_RE = re.compile(
    r"(?:gross\s+receipts|annual\s+(?:gross\s+)?(?:receipts|revenue)|"
    r"(?:300|500)\s+or\s+fewer\s+employees|fewer\s+than\s+\d+\s+employees|"
    r"small\s+business(?:\s+concern)?|SBE\b|SB\s+definition|"
    r"vendor\s+eligibility|eligible\s+(?:as|if)\s+a\s+small|"
    r"NAICS|size\s+standard)",
    re.IGNORECASE,
)
_EVAL_ANCHOR_RE = re.compile(
    r"(?:points?\s+will\s+be\s+awarded|evaluation\s+criteria|"
    r"selection\s+criteria|scoring\s+(?:criteria|factors|matrix)|"
    r"maximum\s+points|point\s+allocation|weighted\s+as\s+follows|"
    r"weighted\s+criteria|criteria\s+and\s+points|"
    r"total\s+(?:of\s+)?100\s+points)",
    re.IGNORECASE,
)
# Require the literal word points/pts after the number — never bare "3" from "3 years".
_EVAL_ROW_RE = re.compile(
    r"(?P<label>"
    r"Overall\s+Capabilities|"
    r"Brand\s+Marketing\s+Plan|"
    r"Familiarity\s+with\s+(?:the\s+)?Hawai.?i\s+Brand|"
    r"Familiarity\s+with.{0,40}Brand|"
    r"Cost\s+Points?\s+Conversion|"
    r"Price\s+Reasonableness|"
    r"Price|"
    r"Portfolio|"
    r"Technical\s+(?:Approach|Proposal|Capability)|"
    r"Cost(?:\s*/\s*Price)?|"
    r"Experience|"
    r"Qualifications|"
    r"References|"
    r"Oral\s+Presentation|"
    r"Interview"
    r")"
    r".{0,80}?"
    r"(?P<pts>\d{1,3})\s*(?:points?|pts\.?)\b",
    re.IGNORECASE | re.DOTALL,
)
_EVAL_POINTS_LINE_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9/ &'’\-]{3,80}?)"
    r"\s*[:\-|–—]\s*"
    r"(?P<pts>\d{1,3})\s*(?:points?|pts\.?)\b",
    re.IGNORECASE,
)
# Percent-weighted tables (e.g. NYCEDC V.B: four criteria at 25% each).
_EVAL_PERCENT_LINE_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9/ &'’\-,\.]{3,100}?)"
    r"\s*(?:[:\-–—]|\()\s*"
    r"(?P<pct>\d{1,3})\s*%\s*\)?",
    re.IGNORECASE,
)
_EVAL_PERCENT_SKIP_LABEL = re.compile(
    r"(?i)\b(?:mwbe|mbe|wbe|dbes?|participation|gross\s+receipts|"
    r"workforce|fte|time\s+allocation|of\s+their\s+time|"
    r"discount|contingency|retainage|overhead)\b"
)


def money_to_number(amount: str, suffix: str | None) -> float | None:
    try:
        base = float(amount.replace(",", ""))
    except ValueError:
        return None
    suf = (suffix or "").casefold()
    if suf in {"million", "m"}:
        return base * 1_000_000
    if suf in {"billion", "b"}:
        return base * 1_000_000_000
    if suf in {"thousand", "k"}:
        return base * 1_000
    return base


def format_money(value: float) -> str:
    if value >= 1_000_000 and value % 1_000_000 == 0:
        return f"${value/1_000_000:.0f}M"
    if value >= 1_000_000:
        return f"${value:,.0f}"
    return f"${value:,.0f}"


def _is_eligibility_dollar_context(window: str) -> bool:
    return bool(_ELIGIBILITY_DOLLAR_CONTEXT_RE.search(window or ""))


def _normalize_eval_label(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip(" .-:").casefold()


def _dedupe_evaluation_rows(
    rows: list[tuple[str, int]],
    *,
    unit: str = "points",
) -> list[str]:
    """Keep unique labels; drop conflicting duplicates (fabrication / bad extract signal)."""
    by_label: dict[str, int] = {}
    order: list[str] = []
    conflicts: set[str] = set()
    for label, pts in rows:
        key = _normalize_eval_label(label)
        if not key:
            continue
        if key in by_label and by_label[key] != pts:
            conflicts.add(key)
            continue
        if key not in by_label:
            order.append(label)
            by_label[key] = pts
    suffix = "%" if unit == "percent" else " points"
    lines: list[str] = []
    for label in order:
        key = _normalize_eval_label(label)
        if key in conflicts:
            continue
        lines.append(f"{label}: {by_label[key]}{suffix}")
    return lines


def _parse_eval_weight(line: str) -> int | None:
    try:
        token = line.rsplit(":", 1)[1].strip().split()[0]
        return int(token.rstrip("%"))
    except (IndexError, ValueError):
        return None


def evaluation_table_is_reliable(facts: dict[str, Any]) -> bool:
    """True when extracted rows look like a published point or percent table."""
    lines = facts.get("evaluation_lines") or []
    if len(lines) < 3:
        return False
    total = int(facts.get("evaluation_total") or 0)
    # Point tables are usually ~50–100+; percent tables sum near 100.
    if total < 40:
        return False
    labels = [_normalize_eval_label(line.split(":", 1)[0]) for line in lines]
    if len(labels) != len(set(labels)):
        return False
    return True


def extract_rfp_hard_facts(text: str) -> dict[str, Any]:
    """Pull contract value + evaluation point rows from the FULL RFP body."""
    body = text or ""
    contract_lines: list[str] = []
    other_dollars: list[str] = []
    eligibility_dollars: list[str] = []
    seen_money: set[str] = set()

    for match in _YEAR_BUDGET_RE.finditer(body):
        year_bit = re.sub(r"\s+", " ", match.group(0)).strip()
        start = max(0, match.start() - 120)
        end = min(len(body), match.end() + 80)
        if _is_eligibility_dollar_context(body[start:end]):
            continue
        if len(year_bit) > 160:
            year_bit = year_bit[:157] + "…"
        if year_bit.casefold() not in {x.casefold() for x in contract_lines}:
            contract_lines.append(year_bit)

    for match in _MONEY_RE.finditer(body):
        raw = match.group(0)
        amount, suffix = match.group(1), match.group(2)
        value = money_to_number(amount, suffix)
        if value is None:
            continue
        display = raw if raw.startswith("$") else f"${raw}"
        key = f"{value:.0f}"
        if key in seen_money:
            continue
        seen_money.add(key)
        start = max(0, match.start() - 120)
        end = min(len(body), match.end() + 80)
        window = body[start:end]
        if _is_eligibility_dollar_context(window):
            snippet = re.sub(r"\s+", " ", window).strip()
            if len(snippet) > 160:
                snippet = snippet[:157] + "…"
            eligibility_dollars.append(
                f"{format_money(value)} — vendor/small-business eligibility context (NOT contract value): {snippet}"
            )
            continue
        if _CEILING_CONTEXT_RE.search(window) and value > 0:
            snippet = re.sub(r"\s+", " ", window).strip()
            if len(snippet) > 180:
                snippet = snippet[:177] + "…"
            contract_lines.append(f"{format_money(value)} — context: {snippet}")
        elif value > 0:
            other_dollars.append(display.strip())

    contract_lines = list(dict.fromkeys(contract_lines))[:12]
    other_dollars = [d for d in dict.fromkeys(other_dollars) if d][:12]
    eligibility_dollars = list(dict.fromkeys(eligibility_dollars))[:8]

    collected_points: list[tuple[str, int]] = []
    collected_percent: list[tuple[str, int]] = []

    # Only search inside windows anchored to real scoring-language — never whole-doc freestyle.
    for m in _EVAL_ANCHOR_RE.finditer(body):
        window = body[max(0, m.start() - 200) : min(len(body), m.start() + 5000)]
        for match in _EVAL_ROW_RE.finditer(window):
            label = re.sub(r"\s+", " ", match.group("label")).strip()
            pts = int(match.group("pts"))
            # Allow 1000-point RFP scales (e.g. Price 350 of 1000) — not only ≤100.
            if pts <= 0 or pts > 2000:
                continue
            collected_points.append((label, pts))
        for row_m in _EVAL_POINTS_LINE_RE.finditer(window):
            label = re.sub(r"\s+", " ", row_m.group("label")).strip(" .-:")
            pts = int(row_m.group("pts"))
            if pts <= 0 or pts > 2000:
                continue
            if len(label) < 4 or label.casefold() in {"section", "page", "item", "group"}:
                continue
            collected_points.append((label, pts))
        for row_m in _EVAL_PERCENT_LINE_RE.finditer(window):
            label = re.sub(r"\s+", " ", row_m.group("label")).strip(" .-:()")
            pct = int(row_m.group("pct"))
            if pct <= 0 or pct > 100:
                continue
            if len(label) < 4 or label.casefold() in {"section", "page", "item", "group"}:
                continue
            if _EVAL_PERCENT_SKIP_LABEL.search(label):
                continue
            collected_percent.append((label, pct))

    # Prefer an explicit point table; fall back to percent-weighted selection criteria.
    if collected_points:
        evaluation_lines = _dedupe_evaluation_rows(collected_points, unit="points")[:16]
    else:
        evaluation_lines = _dedupe_evaluation_rows(
            collected_percent, unit="percent"
        )[:16]
    total_pts = 0
    for line in evaluation_lines:
        weight = _parse_eval_weight(line)
        if weight is not None:
            total_pts += weight

    from app.services.go_no_go_opportunity import classify_opportunity

    opportunity_class, compensation_signal = classify_opportunity(body)
    # Contract ceiling lines are authoritative confirmed fee even if prose is thin.
    if contract_lines and compensation_signal == "undisclosed":
        compensation_signal = "confirmed_fee"

    facts = {
        "contract_value_lines": contract_lines,
        "other_dollar_amounts": other_dollars,
        "eligibility_dollar_lines": eligibility_dollars,
        "evaluation_lines": evaluation_lines,
        "evaluation_total": total_pts if total_pts > 0 else None,
        "opportunity_class": opportunity_class,
        "compensation_signal": compensation_signal,
    }
    # Drop unreliable / thin false-positive tables entirely.
    if not evaluation_table_is_reliable(facts):
        facts["evaluation_lines"] = []
        facts["evaluation_total"] = None
    return facts


def format_hard_facts_block(facts: dict[str, Any]) -> str:
    """Markdown block for proposal / Go-No-Go prompts."""
    lines = ["## HARD FACTS (from full RFP text — cite exactly; never invent 'undisclosed')"]
    contracts = facts.get("contract_value_lines") or []
    if contracts:
        lines.append("### Contract value / ceiling")
        lines.extend(f"- {c}" for c in contracts)
    else:
        lines.append("### Contract value / ceiling")
        lines.append("- Not found as a contract ceiling/budget in the RFP body.")

    # Structured money authority (hard NTE vs program/media envelope) — RFP-agnostic.
    try:
        from app.services.evidence_trust.rfp_money_constraints import (
            extract_rfp_money_constraints,
            format_money_constraints_block,
        )

        # Caller may pass full RFP via facts["_source_text"]; else skip structured block.
        source = facts.get("_source_text") or ""
        if source.strip():
            money = extract_rfp_money_constraints(source)
            lines.append(format_money_constraints_block(money))
    except Exception:
        pass
    evals = facts.get("evaluation_lines") or []
    if evals:
        lines.append("### Evaluation criteria (points or % weights)")
        lines.extend(f"- {e}" for e in evals)
        total = facts.get("evaluation_total")
        if total:
            lines.append(f"- Extracted weight sum (may overlap): {total}")
        lines.append(
            "- These weights ARE disclosed in the RFP — cite them for Win Probability; "
            "do NOT claim the evaluation table is undisclosed."
        )
    else:
        lines.append("### Evaluation criteria (points or % weights)")
        lines.append(
            "- No disclosed point/percent-weight table found. Do NOT invent Category/Max Points "
            "or percentages. Describe pass/fail + scored question groups only."
        )
    eligibility = facts.get("eligibility_dollar_lines") or []
    if eligibility:
        lines.append("### Vendor/small-business eligibility dollars (NOT contract value)")
        lines.extend(f"- {d}" for d in eligibility[:6])
        lines.append(
            "- NEVER cite these as contract value, ceiling, or opportunity size."
        )
    others = facts.get("other_dollar_amounts") or []
    if others:
        lines.append("### Other dollar amounts mentioned")
        lines.extend(f"- {d}" for d in others[:8])

    opp = facts.get("opportunity_class")
    comp = facts.get("compensation_signal")
    if opp or comp:
        lines.append("### Opportunity shape (deterministic — score from this)")
        if opp:
            lines.append(f"- Opportunity class: {opp}")
        if comp:
            lines.append(f"- Compensation signal: {comp}")
        lines.append(
            "- open_competition without confirmed_fee → Financial 0, Worth ≤1, "
            "Strategic ≤2, Win ≤2, prefer no_go (not a paid services engagement)."
        )
        lines.append(
            "- professional_services + undisclosed budget → Worth ~3 allowed; "
            "do not invent a fee and do not force Financial to 0 solely for undisclosed budget."
        )

    lines.append(
        "If a contract ceiling or evaluation point row appears above, cite it. "
        "If not, say undisclosed — never invent, never re-label eligibility thresholds as budget."
    )
    return "\n".join(lines)
