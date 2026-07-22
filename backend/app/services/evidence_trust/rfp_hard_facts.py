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
    r"scoring\s+(?:criteria|factors|matrix)|maximum\s+points|"
    r"point\s+allocation|weighted\s+as\s+follows|"
    r"criteria\s+and\s+points|total\s+(?:of\s+)?100\s+points)",
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


def _dedupe_evaluation_rows(rows: list[tuple[str, int]]) -> list[str]:
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
    lines: list[str] = []
    for label in order:
        key = _normalize_eval_label(label)
        if key in conflicts:
            continue
        lines.append(f"{label}: {by_label[key]} points")
    return lines


def evaluation_table_is_reliable(facts: dict[str, Any]) -> bool:
    """True only when extracted rows look like a real published point table."""
    lines = facts.get("evaluation_lines") or []
    if len(lines) < 3:
        return False
    total = int(facts.get("evaluation_total") or 0)
    # Published public-sector tables are usually ~50–100+; tiny totals are false hits.
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
        if value >= 100_000 and _CEILING_CONTEXT_RE.search(window):
            snippet = re.sub(r"\s+", " ", window).strip()
            if len(snippet) > 180:
                snippet = snippet[:177] + "…"
            contract_lines.append(f"{format_money(value)} — context: {snippet}")
        elif value >= 10_000:
            other_dollars.append(display.strip())

    contract_lines = list(dict.fromkeys(contract_lines))[:12]
    other_dollars = [d for d in dict.fromkeys(other_dollars) if d][:12]
    eligibility_dollars = list(dict.fromkeys(eligibility_dollars))[:8]

    collected: list[tuple[str, int]] = []

    # Only search inside windows anchored to real scoring-language — never whole-doc freestyle.
    for m in _EVAL_ANCHOR_RE.finditer(body):
        window = body[max(0, m.start() - 200) : min(len(body), m.start() + 5000)]
        for match in _EVAL_ROW_RE.finditer(window):
            label = re.sub(r"\s+", " ", match.group("label")).strip()
            pts = int(match.group("pts"))
            if pts <= 0 or pts > 100:
                continue
            collected.append((label, pts))
        for row_m in _EVAL_POINTS_LINE_RE.finditer(window):
            label = re.sub(r"\s+", " ", row_m.group("label")).strip(" .-:")
            pts = int(row_m.group("pts"))
            if pts <= 0 or pts > 100:
                continue
            if len(label) < 4 or label.casefold() in {"section", "page", "item", "group"}:
                continue
            collected.append((label, pts))

    evaluation_lines = _dedupe_evaluation_rows(collected)[:16]
    total_pts = 0
    for line in evaluation_lines:
        try:
            total_pts += int(line.rsplit(":", 1)[1].strip().split()[0])
        except (IndexError, ValueError):
            continue

    facts = {
        "contract_value_lines": contract_lines,
        "other_dollar_amounts": other_dollars,
        "eligibility_dollar_lines": eligibility_dollars,
        "evaluation_lines": evaluation_lines,
        "evaluation_total": total_pts if total_pts > 0 else None,
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
    evals = facts.get("evaluation_lines") or []
    if evals:
        lines.append("### Evaluation criteria (points)")
        lines.extend(f"- {e}" for e in evals)
        total = facts.get("evaluation_total")
        if total:
            lines.append(f"- Extracted point sum (may overlap): {total}")
    else:
        lines.append("### Evaluation criteria (points)")
        lines.append(
            "- No disclosed point-weight table found. Do NOT invent Category/Max Points "
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
    lines.append(
        "If a contract ceiling or evaluation point row appears above, cite it. "
        "If not, say undisclosed — never invent, never re-label eligibility thresholds as budget."
    )
    return "\n".join(lines)
