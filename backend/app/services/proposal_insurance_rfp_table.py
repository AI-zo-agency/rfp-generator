"""Align mandatory insurance limits tables with RFP-required coverages (e.g. E&O)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection

logger = logging.getLogger(__name__)

_EO_REQUIREMENT_RE = re.compile(
    r"(?:errors?\s*(?:and|&)\s*omissions?(?:\s+insurance)?|professional\s+liability|E&O)"
    r".{0,200}?\$[\d,]+(?:\.\d+)?(?:\s*(?:million|m\b))?"
    r".{0,200}?(?:aggregate|\$[\d,]+)",
    re.I | re.S,
)

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.M)


def extract_eo_requirement_line(rfp_text: str) -> str | None:
    text = rfp_text or ""
    m = _EO_REQUIREMENT_RE.search(text)
    if not m:
        return None
    snippet = re.sub(r"\s+", " ", m.group(0)).strip()
    return snippet[:500]


def _table_mentions_eo(content: str) -> bool:
    lower = content.casefold()
    return any(
        k in lower
        for k in (
            "professional liability",
            "errors and omissions",
            "errors & omissions",
            "e&o",
        )
    )


def _insert_eo_table_row(content: str, requirement_line: str) -> str:
    """Add E&O row to first markdown limits table if missing."""
    if _table_mentions_eo(content):
        return content

    lines = content.splitlines()
    insert_at: int | None = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and "---" in lines[i + 1] if i + 1 < len(lines) else False:
            insert_at = i + 2
            break
        if "coverage" in line.casefold() and "limit" in line.casefold() and "|" in line:
            insert_at = i + 1
            break

    row = (
        "| **Professional Liability / E&O (per RFP)** | "
        f"{requirement_line} — zö policy limits [MANUAL FILL: attach COI] |"
    )
    if insert_at is None:
        block = (
            "\n\n## Required Insurance Coverage & Minimum Limits (RFP)\n\n"
            "| Coverage | RFP minimum / status |\n"
            "| --- | --- |\n"
            f"{row}\n"
        )
        return content.rstrip() + block

    lines.insert(insert_at, row)
    return "\n".join(lines)


def _insurance_sections(draft: ProposalDraft) -> list[tuple[int, ProposalSection]]:
    hits: list[tuple[int, ProposalSection]] = []
    for idx, section in enumerate(draft.sections):
        title_cf = (section.title or "").casefold()
        sid = section.id.casefold()
        blob = f"{title_cf}\n{section.content or ''}".casefold()
        if sid == "section-1-insurance" or "insurance" in title_cf:
            hits.append((idx, section))
        elif "required insurance" in blob or "minimum limits" in blob:
            hits.append((idx, section))
        elif section.id.startswith("rfp-closing") and "insurance" in title_cf:
            hits.append((idx, section))
    return hits


_INSURANCE_SILENT_DENIAL_RE = re.compile(
    r"(?:rfp|excerpt|solicitation).{0,80}(?:does not|did not|do not)\s+"
    r"(?:enumerate|specify|state).{0,120}(?:minimum\s+)?insurance",
    re.I | re.S,
)

_CGL_LIMITS_RE = re.compile(
    r"commercial general liability.{0,400}?"
    r"(\$[\d,]+(?:\.\d+)?\s*(?:million|m\b)?).{0,120}?"
    r"(\$[\d,]+(?:\.\d+)?\s*(?:million|m\b)?(?:\s*aggregate)?)",
    re.I | re.S,
)

_AUTO_LIMIT_RE = re.compile(
    r"(?:automobile|auto(?:mobile)?)\s+liability.{0,200}?"
    r"(\$[\d,]+(?:\.\d+)?\s*(?:million|m\b)?)",
    re.I | re.S,
)

_EO_LIMITS_RE = re.compile(
    r"(?:errors?\s*(?:and|&)\s*omissions?|professional liability|E&O).{0,400}?"
    r"(\$[\d,]+(?:\.\d+)?\s*(?:million|m\b)?).{0,120}?"
    r"(\$[\d,]+(?:\.\d+)?\s*(?:million|m\b)?(?:\s*aggregate)?)",
    re.I | re.S,
)


@dataclass(frozen=True)
class RfpInsuranceLimits:
    cgl: str = ""
    auto: str = ""
    eo: str = ""
    source_note: str = ""


def parse_rfp_insurance_limits(rfp_text: str) -> RfpInsuranceLimits | None:
    """Best-effort parse of minimum limits from full RFP text (e.g. Section 5.9)."""
    text = rfp_text or ""
    if len(text) < 100:
        return None

    window = text
    m59 = re.search(r"5\.9.{0,12}insurance", text, re.I | re.S)
    if m59:
        start = max(0, m59.start() - 200)
        window = text[start : min(len(text), m59.start() + 9000)]

    cgl = auto = eo = ""
    mc = _CGL_LIMITS_RE.search(window) or _CGL_LIMITS_RE.search(text)
    if mc:
        cgl = f"{mc.group(1).strip()} per occurrence / {mc.group(2).strip()} aggregate (CGL per RFP)"
    ma = _AUTO_LIMIT_RE.search(window) or _AUTO_LIMIT_RE.search(text)
    if ma:
        auto = f"{ma.group(1).strip()} (automobile liability per RFP)"
    me = _EO_LIMITS_RE.search(window) or _EO_LIMITS_RE.search(text)
    if me:
        eo = (
            f"{me.group(1).strip()} per occurrence / {me.group(2).strip()} aggregate "
            "(E&O / professional liability per RFP)"
        )

    if not any((cgl, auto, eo)):
        return None
    return RfpInsuranceLimits(
        cgl=cgl,
        auto=auto,
        eo=eo,
        source_note="Parsed from RFP insurance requirements (e.g. Section 5.9).",
    )


def _limits_markdown_table(limits: RfpInsuranceLimits) -> str:
    rows = ["| Coverage | RFP minimum (per solicitation) |", "| --- | --- |"]
    if limits.cgl:
        rows.append(f"| Commercial General Liability | {limits.cgl} |")
    if limits.auto:
        rows.append(f"| Automobile Liability | {limits.auto} |")
    if limits.eo:
        rows.append(f"| Professional Liability / E&O | {limits.eo} |")
    rows.append("| Certificates of Insurance | [MANUAL FILL: attach COI naming HTA as additional insured] |")
    return "\n".join(rows)


def _strip_insurance_silent_denial(content: str) -> str:
    return _INSURANCE_SILENT_DENIAL_RE.sub("", content or "").strip()


def repair_insurance_minimum_limits(
    draft: ProposalDraft,
    *,
    rfp_text: str,
) -> tuple[ProposalDraft, list[str]]:
    """Replace 'RFP does not enumerate limits' when Section 5.9 limits are in the PDF text."""
    limits = parse_rfp_insurance_limits(rfp_text)
    if not limits:
        return draft, ["Insurance limits: could not parse Section 5.9-style minimums from RFP text."]

    logs: list[str] = []
    sections = list(draft.sections)
    changed = False
    table_block = (
        "\n\n## Required Insurance Coverage & Minimum Limits (RFP Section 5.9)\n\n"
        f"{_limits_markdown_table(limits)}\n"
    )

    for idx, section in _insurance_sections(draft):
        content = section.content or ""
        if not _INSURANCE_SILENT_DENIAL_RE.search(content) and "does not enumerate" not in content.casefold():
            if limits.cgl and "commercial general liability" in content.casefold() and "$3" in content:
                continue
            if "section 5.9" in content.casefold() and limits.cgl:
                continue
        updated = _strip_insurance_silent_denial(content)
        if _INSURANCE_SILENT_DENIAL_RE.search(content) or "does not enumerate specific minimum" in content.casefold():
            updated = re.sub(
                r"[^.\n]*does not enumerate[^.\n]*minimum insurance limits[^.\n]*\.?\s*",
                "",
                updated,
                flags=re.I,
            )
        if table_block.strip() not in updated:
            updated = updated.rstrip() + table_block
        if updated != content:
            sections[idx] = section.model_copy(
                update={"content": updated, "status": "generated"}
            )
            changed = True
            logs.append(f"Insurance limits aligned to RFP text: {section.title}")

    if not changed:
        return draft, logs or ["Insurance section already states RFP minimum limits."]

    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


def repair_insurance_eo_table(
    draft: ProposalDraft,
    *,
    rfp_text: str,
) -> tuple[ProposalDraft, list[str]]:
    req = extract_eo_requirement_line(rfp_text)
    if not req:
        return draft, ["No E&O/Professional Liability requirement detected in RFP text."]

    logs: list[str] = []
    sections = list(draft.sections)
    changed = False

    for idx, section in _insurance_sections(draft):
        content = section.content or ""
        if _table_mentions_eo(content) and "aggregate" in content.casefold():
            continue
        updated = _insert_eo_table_row(content, req)
        if updated != content:
            sections[idx] = section.model_copy(
                update={"content": updated, "status": "generated"}
            )
            changed = True
            logs.append(f"Added E&O/Professional Liability row to: {section.title}")

    if not changed:
        return draft, logs or ["E&O already present in insurance table(s)."]

    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
