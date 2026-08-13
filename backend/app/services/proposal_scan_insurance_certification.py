"""Complete Scan — gate unsigned insurance compliance certifications.

Principle: a signed Exception / compliance form that marks coverages "Compliant"
or asserts "meets or exceeds" RFP insurance minimums is a legal certification.
Those claims must be grounded in what Section 1.5 (or companyfacts evidence)
actually lists. If 1.5 omits a line (e.g. Automobile) or never states a limit
the form certifies, rewrite to MANUAL FILL for Sonja — never invent Compliant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.proposal import ProposalDraft, ProposalSection

# Standard coverage *categories* (not platform synonym maps). Used only to
# compare Exception Form rows against Section 1.5 inventory language.
_COVERAGE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "general liability",
        (
            "general liability",
            "commercial general liability",
            "cgl",
        ),
    ),
    (
        "automobile liability",
        (
            "automobile liability",
            "auto liability",
            "commercial auto",
            "automobile",
        ),
    ),
    (
        "professional liability",
        (
            "professional liability",
            "errors and omissions",
            "errors & omissions",
            "e&o",
        ),
    ),
    (
        "workers compensation",
        (
            "workers compensation",
            "workers' compensation",
            "worker's compensation",
            "workers comp",
        ),
    ),
    (
        "cyber liability",
        (
            "cyber liability",
            "cyber insurance",
            "network security",
        ),
    ),
    (
        "umbrella",
        (
            "umbrella",
            "excess liability",
        ),
    ),
)

_DOLLAR_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|m\b)?",
    re.I,
)

_MEETS_OR_EXCEEDS_RE = re.compile(
    r"(?is)"
    r"(?:currently\s+maintains|maintains|carry|carries|has)\s+"
    r"(?:insurance\s+)?coverage\s+"
    r"(?:meeting|that\s+meets|meets)\s+(?:or\s+exceeding\s+)?"
    r"(?:the\s+)?"
    r"(?:[A-Za-z0-9&./'-]+(?:\s+[A-Za-z0-9&./'-]+){0,4}\s+)?"
    r"(?:stated\s+)?(?:requirements?|minimums?|limits?)",
)

_NO_EXCEPTIONS_RE = re.compile(
    r"(?is)no\s+exceptions?(?:,\s*clarifications?)?\s*"
    r"(?:or\s+alternative\s+language\s+)?(?:are\s+)?(?:requested|taken|taken\.)",
)

_COMPLIANT_CELL_RE = re.compile(r"(?i)\bCompliant\b")

_MANUAL_COVERAGE = (
    "[MANUAL FILL: Sonja — confirm this coverage type & limits on current COI "
    "before signing; do not certify Compliant unless verified]"
)

_MANUAL_MEETS = (
    "[MANUAL FILL: Sonja — confirm current policies meet RFP minimums (incl. any "
    "Automobile / aggregate limits) on the COI before signing. Do not certify "
    "\"meets or exceeds\" until verified. If coverage is short, take an exception "
    "or commit to bind before contract execution.]"
)


@dataclass
class InsuranceInventory:
    """Coverage types + dollar figures stated in Section 1.5 / evidence."""

    categories: set[str] = field(default_factory=set)
    dollars: set[int] = field(default_factory=set)
    source_title: str = ""


def _normalize_dollars(text: str) -> set[int]:
    out: set[int] = set()
    for match in _DOLLAR_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        unit = (match.group(2) or "").casefold()
        if unit.startswith("m"):
            value *= 1_000_000
        out.add(int(value))
    return out


def coverage_categories_mentioned(text: str) -> set[str]:
    blob = (text or "").casefold()
    found: set[str] = set()
    for canonical, aliases in _COVERAGE_CATEGORIES:
        if any(alias in blob for alias in aliases):
            found.add(canonical)
    return found


def build_insurance_inventory(draft: ProposalDraft) -> InsuranceInventory:
    """Prefer Section 1.5; fall back to any insurance narrative tab."""
    preferred: ProposalSection | None = None
    fallback: ProposalSection | None = None
    for section in draft.sections:
        sid = (section.id or "").casefold()
        title_cf = (section.title or "").casefold()
        if sid == "section-1-insurance" or title_cf.startswith("1.5"):
            preferred = section
            break
        if fallback is None and "insurance" in title_cf and "exception" not in title_cf:
            fallback = section
    source = preferred or fallback
    if not source or not (source.content or "").strip():
        return InsuranceInventory()
    body = source.content or ""
    return InsuranceInventory(
        categories=coverage_categories_mentioned(body),
        dollars=_normalize_dollars(body),
        source_title=source.title or source.id,
    )


def is_insurance_certification_section(section: ProposalSection) -> bool:
    """True for Exception Forms / compliance tables that certify insurance."""
    title_cf = (section.title or "").casefold()
    body = section.content or ""
    body_cf = body.casefold()
    sid = (section.id or "").casefold()

    if sid == "section-1-insurance" or title_cf.startswith("1.5"):
        return False

    title_hit = any(
        hint in title_cf
        for hint in (
            "exception",
            "exceptions",
            "insurance compliance",
            "offeror certification",
            "vendor certification",
            "acknowledgment",
            "acknowledgement",
            "compliance form",
            "minimum insurance",
            "required insurance",
        )
    )
    body_hit = bool(
        _MEETS_OR_EXCEEDS_RE.search(body)
        or (
            "insurance" in body_cf
            and (
                _COMPLIANT_CELL_RE.search(body)
                or _NO_EXCEPTIONS_RE.search(body)
                or "no exceptions" in body_cf
            )
        )
    )
    return title_hit or body_hit


def _row_coverage_category(row: str) -> str | None:
    cats = coverage_categories_mentioned(row)
    if not cats:
        return None
    # Prefer the most specific non-umbrella match in the row.
    for canonical, _ in _COVERAGE_CATEGORIES:
        if canonical in cats:
            return canonical
    return next(iter(cats))


def _gate_compliant_cells(
    content: str,
    inventory: InsuranceInventory,
) -> tuple[str, list[str]]:
    logs: list[str] = []
    lines = content.splitlines()
    out: list[str] = []
    for line in lines:
        if "|" not in line or not _COMPLIANT_CELL_RE.search(line):
            out.append(line)
            continue
        category = _row_coverage_category(line)
        dollars = _normalize_dollars(line)
        needs_gate = False
        reason = ""
        if category and category not in inventory.categories:
            needs_gate = True
            reason = (
                f"{category} marked Compliant but not listed in "
                f"{inventory.source_title or 'Section 1.5'}"
            )
        elif dollars and inventory.dollars and not (dollars & inventory.dollars):
            needs_gate = True
            reason = (
                f"limit(s) {sorted(dollars)} certified Compliant but not stated in "
                f"{inventory.source_title or 'Section 1.5'}"
            )
        elif category and not inventory.categories:
            needs_gate = True
            reason = (
                f"{category} marked Compliant with empty insurance inventory — "
                "require COI verification"
            )
        elif dollars and not inventory.dollars:
            # 1.5 lists types only (no $) — cannot certify specific RFP $ figures.
            needs_gate = True
            reason = (
                "specific dollar limits certified Compliant but Section 1.5 has no "
                "verified dollar amounts"
            )

        if needs_gate:
            gated = _COMPLIANT_CELL_RE.sub(_MANUAL_COVERAGE, line, count=1)
            out.append(gated)
            logs.append(reason)
        else:
            out.append(line)
    return "\n".join(out), logs


def gate_section_insurance_certification(
    section: ProposalSection,
    inventory: InsuranceInventory,
) -> tuple[ProposalSection, list[str]]:
    """Rewrite unverified insurance Compliant / meets-or-exceeds claims."""
    if not is_insurance_certification_section(section):
        return section, []

    body = section.content or ""
    logs: list[str] = []
    new_body = body

    if _MEETS_OR_EXCEEDS_RE.search(new_body):
        new_body = _MEETS_OR_EXCEEDS_RE.sub(_MANUAL_MEETS, new_body, count=1)
        logs.append(
            "Gated 'meets or exceeds insurance requirements' → MANUAL FILL for Sonja/COI"
        )

    gated_table, table_logs = _gate_compliant_cells(new_body, inventory)
    if table_logs:
        new_body = gated_table
        logs.extend(table_logs)

    # "No exceptions" + insurance certification language is itself a certification.
    if logs and _NO_EXCEPTIONS_RE.search(new_body):
        note = (
            "\n\n[MANUAL FILL: Sonja — Exception Form currently asserts no exceptions "
            "while insurance Compliant claims are unverified against Section 1.5 / COI. "
            "Confirm coverage or take a real exception before signature.]\n"
        )
        if "insurance Compliant claims are unverified" not in new_body:
            new_body = new_body.rstrip() + note
            logs.append("Flagged 'No exceptions' while insurance claims remain unverified")

    if new_body == body:
        return section, []
    return section.model_copy(update={"content": new_body, "status": "generated"}), logs


def gate_draft_insurance_certifications(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Run insurance certification gate across the manuscript.

    Returns (draft, logs, human_decision_gaps).
    """
    inventory = build_insurance_inventory(draft)
    logs: list[str] = []
    human: list[str] = []
    sections: list[ProposalSection] = []
    changed = False

    for section in draft.sections:
        updated, section_logs = gate_section_insurance_certification(section, inventory)
        if section_logs:
            changed = True
            for line in section_logs:
                logs.append(f"{section.title or section.id}: {line}")
            human.append(
                f"{section.title or section.id}: Insurance compliance claims must be "
                "verified against the current COI with Sonja before signature — "
                "do not certify Compliant / meets-or-exceeds without proof."
            )
            sections.append(updated)
        else:
            sections.append(section)

    if not changed:
        return draft, logs, human
    # Dedupe human gaps while preserving order
    seen: set[str] = set()
    unique_human: list[str] = []
    for item in human:
        if item not in seen:
            seen.add(item)
            unique_human.append(item)
    return draft.model_copy(update={"sections": sections}), logs, unique_human
