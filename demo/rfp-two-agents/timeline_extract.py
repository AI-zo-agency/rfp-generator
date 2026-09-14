"""Timeline extraction resilient to PDF line breaks (no LLM)."""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent1_tools import RfpDoc

_MONTH_DATE = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}"
)
_TIME_TAIL = r"(?:\s+prior\s+to\s+\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|AM|PM)[^.\n]{0,40})?"


def normalize_rfp_text(text: str) -> str:
    """Repair common PDF extraction breaks."""
    t = text or ""
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    t = re.sub(r"(\w)\n(\w)", lambda m: m.group(1) + " " + m.group(2) if m.group(1).islower() else m.group(0), t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _find_due_phrase(blob: str, *, kind: str) -> str | None:
    """kind: questions | quotes"""
    if kind == "questions":
        lead = r"(?:questions?(?:\s+(?:regarding|about|relating\s+to))?\s+(?:this\s+)?(?:rfq|rfp|solicitation)[^.\n]{0,60}?\s+due|questions?\s+due)"
    else:
        lead = r"(?:(?:sealed\s+)?(?:quotes?|proposals?|responses?|submittals?)\s+due|quote\s+due|quotes?\s+must\s+be\s+received)"
    pat = re.compile(
        rf"{lead}[^{{}}]*?(?:{_MONTH_DATE}){_TIME_TAIL}?",
        re.I | re.S,
    )
    m = pat.search(blob)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip()[:240]
    # Fallback: month date near keyword within window
    for m in re.finditer(_MONTH_DATE, blob, re.I):
        start = max(0, m.start() - 120)
        window = blob[start : m.end() + 80].casefold()
        if kind == "questions" and "question" in window and "due" in window:
            return re.sub(r"\s+", " ", blob[start : m.end() + 80]).strip()[:240]
        if kind == "quotes" and any(k in window for k in ("quote", "proposal", "response")) and "due" in window:
            return re.sub(r"\s+", " ", blob[start : m.end() + 80]).strip()[:240]
    return None


def extract_timeline_intel(doc: "RfpDoc", pack: dict[str, Any]) -> dict[str, Any]:
    parts: list[str] = []
    for sec in (pack.get("boundedSections") or {}).values():
        if isinstance(sec, dict) and sec.get("text"):
            parts.append(str(sec["text"]))
    for key in ("key_dates", "contract_term", "submission_instructions"):
        sec = (pack.get("sections") or {}).get(key) or {}
        if sec.get("text"):
            parts.append(str(sec["text"]))
    parts.extend(str(p) for p in doc.pages[:25])
    blob = normalize_rfp_text("\n".join(parts))

    out: dict[str, Any] = {}
    q = _find_due_phrase(blob, kind="questions")
    if q:
        out["questionsDue"] = q
    qu = _find_due_phrase(blob, kind="quotes")
    if qu:
        out["quotesDue"] = qu

    term = re.search(
        rf"(?:contract\s+term|period\s+of\s+performance|commenc(?:e|ing))[^.\n]{{0,80}}?(?:{_MONTH_DATE})",
        blob,
        re.I,
    )
    if term:
        out["initialTermStart"] = re.search(_MONTH_DATE, term.group(0), re.I).group(0)  # type: ignore[union-attr]

    opt = re.search(
        r"(\bfour\b|\b4\b)[^.\n]{0,40}(?:one[- ]year|1[- ]year)\s+option",
        blob,
        re.I,
    )
    if opt:
        out["optionPeriods"] = "Four (4) one-year option periods"
    elif re.search(r"option\s+(?:year|period)", blob, re.I):
        m2 = re.search(
            r"((?:\bfour\b|\b4\b|\bthree\b|\b3\b|\btwo\b|\b2\b)[^\n.]{0,60}option[^\n.]{0,40})",
            blob,
            re.I,
        )
        out["optionPeriods"] = (
            re.sub(r"\s+", " ", m2.group(1)).strip()[:200]
            if m2
            else "Option period(s) as stated in solicitation"
        )

    for hit in pack.get("dateMoneyHits") or []:
        if not isinstance(hit, dict):
            continue
        if hit.get("kind") == "date" and "questionsDue" not in out:
            t = str(hit.get("text") or "")
            if "2026" in t and not out.get("questionsDue"):
                pass  # prefer phrase match
    return out


def merge_timeline_into_understanding(cleaned: dict[str, Any], doc: "RfpDoc", pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    tl = u.get("timelineIntel")
    if not isinstance(tl, dict):
        tl = {}
        u["timelineIntel"] = tl
    extracted = extract_timeline_intel(doc, pack)
    for key, val in extracted.items():
        if val and not tl.get(key):
            tl[key] = val
            fixes.append(f"timelineIntel.{key}:extracted")
    if extracted.get("initialTermStart") and not tl.get("projectStart"):
        # Do NOT infer projectStart from contract term start.
        pass
    # Exact option periods when extract was vague
    if extracted.get("optionPeriods"):
        if "as stated" in str(tl.get("optionPeriods") or "").casefold() or not tl.get("optionPeriods"):
            tl["optionPeriods"] = extracted["optionPeriods"]
            fixes.append("timelineIntel.optionPeriods:prefer_extract")
    return fixes
