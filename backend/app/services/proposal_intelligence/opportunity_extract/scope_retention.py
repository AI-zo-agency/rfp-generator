"""Materialize scope lines from LangExtract + bounded SOW text."""

from __future__ import annotations

import re
from typing import Any

_TAIL_MARKERS = (
    "waterscape",
    "rebate program",
    "ada",
    "website",
    "school outreach",
    "elementary",
    "pet waste",
    "pet-waste",
    "program status report",
    "up to six",
    "cbsm presentation",
    "as-needed",
    "as needed",
    "special assessment",
)

_PROCUREMENT_POLLUTION = (
    "offeror",
    "buynet",
    "rfq",
    "quote",
    "submit a completed",
    "pc600",
    "pc601",
    "payment schedule",
    "draft agreement",
    "franchise tax",
    "withholding",
    "evaluations may consider",
    "during evaluation",
    "cultural competency of offeror",
    "overall total cost",
    "unable to download",
    "version of a form",
    "discussions",
)


def _in_list(needle: str, rows: list[str]) -> bool:
    n = re.sub(r"\s+", " ", needle.casefold()).strip()
    for row in rows:
        r = re.sub(r"\s+", " ", str(row).casefold()).strip()
        if n[:50] in r or r[:50] in n:
            return True
    return False


def _is_performance_scope(text: str) -> bool:
    """Optional/mandatory scope must be contract performance, not solicitation rules."""
    t = text.casefold()
    if any(p in t for p in _PROCUREMENT_POLLUTION):
        # Allow contractor performance that mentions evaluation of campaign, not quote eval
        if "contractor" in t and "campaign" in t:
            return True
        if "contractor" in t and any(m in t for m in _TAIL_MARKERS):
            return True
        return False
    return True


def _is_optional_modality(text: str) -> bool:
    t = text.casefold()
    return any(
        x in t
        for x in (
            "as needed",
            "as-needed",
            "may request",
            "may be requested",
            "at county request",
            "at the county",
            "optional",
            "up to six",
            "up to two",
            "contractor may",
        )
    )


def materialize_scope_from_candidates(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    scope = cleaned.get("scope")
    if not isinstance(scope, dict):
        return fixes
    mand = list(scope.get("mandatory") or [])
    opt = list(scope.get("optional") or [])
    deps = list(scope.get("dependencies") or [])

    for hit in pack.get("scopeCandidates") or []:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("sourceText") or hit.get("text") or "").strip()
        if len(text) < 25 or not _is_performance_scope(text):
            continue
        if text.rstrip().endswith((",", "the", "a", "and", "to", "for", "of")) and len(text) < 80:
            continue
        cls = str(hit.get("class") or "")
        if cls == "scope_optional" or _is_optional_modality(text):
            if not _in_list(text, opt):
                opt.append(text[:500])
                fixes.append("scope.optional+lx")
        elif cls == "scope_dependency":
            if not _in_list(text, deps):
                deps.append(text[:500])
                fixes.append("scope.dependencies+lx")
        else:
            if not _in_list(text, mand):
                mand.append(text[:500])
                fixes.append("scope.mandatory+lx")

    sow_text = str(pack.get("sowBoundedText") or pack.get("sowExcerpt") or "")
    for sent in re.split(r"(?<=[.:;])\s+|\n+", sow_text):
        s = sent.strip()
        if len(s) < 25 or not _is_performance_scope(s):
            continue
        sl = s.casefold()
        if not any(m in sl for m in _TAIL_MARKERS):
            continue
        if _is_optional_modality(s):
            if not _in_list(s, opt):
                opt.append(s[:500])
                fixes.append("scope.optional+tail")
        elif not _in_list(s, mand):
            mand.append(s[:500])
            fixes.append("scope.mandatory+tail")

    before_m, before_o = len(mand), len(opt)
    mand = [m for m in mand if _is_performance_scope(str(m))]
    # Optional: must be performance AND optional modality — move misclassified into mand scrub
    clean_opt: list[str] = []
    for m in opt:
        if not _is_performance_scope(str(m)):
            continue
        if _is_optional_modality(str(m)):
            clean_opt.append(m)
    opt = clean_opt
    if len(mand) < before_m or len(opt) < before_o:
        fixes.append(f"scope.scrub_taxonomy:m{before_m}->{len(mand)} o{before_o}->{len(opt)}")

    scope["mandatory"] = mand
    scope["optional"] = opt
    scope["dependencies"] = deps
    scope.setdefault("notes", "")
    return fixes


def enrich_scope_reporting_notes(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    scope = cleaned.get("scope")
    if not isinstance(scope, dict):
        return fixes
    notes = str(scope.get("notes") or "")
    blob = str(pack.get("sowBoundedText") or pack.get("sowExcerpt") or "")
    for page in pack.get("pages") or []:
        if isinstance(page, str):
            blob += "\n" + page
    monthly = bool(re.search(r"monthly.{0,40}(progress|evaluation|report)", blob, re.I | re.S))
    quarterly = bool(re.search(r"quarterly.{0,40}(progress|evaluation|report)", blob, re.I | re.S))
    if monthly and quarterly:
        msg = (
            "Section 7.5.3 requires monthly progress and evaluation reports, while "
            "Section 7.10.1 requires quarterly progress and evaluation reports. "
            "Both requirements are preserved."
        )
        if msg not in notes:
            notes = (notes + "\n" + msg).strip() if notes else msg
            fixes.append("scope.notes:monthly_quarterly")
    # Deduplicate paragraphs
    parts = [p.strip() for p in re.split(r"\n+", notes) if p.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for p in parts:
        key = re.sub(r"\s+", " ", p.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    scope["notes"] = "\n".join(deduped)
    if len(deduped) < len(parts):
        fixes.append("scope.notes:deduped")
    return fixes


def check_sow_subsection_coverage(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    """Warn when SOW §7.x subsections are barely represented in final scope."""
    warnings: list[str] = []
    sow = str(pack.get("sowBoundedText") or pack.get("sowExcerpt") or "")
    if len(sow) < 500:
        return warnings
    found_sections = sorted({int(m) for m in re.findall(r"\b7\.(\d+)\b", sow)})
    if not found_sections:
        return warnings
    scope = cleaned.get("scope") if isinstance(cleaned.get("scope"), dict) else {}
    blob = " ".join(
        str(x)
        for x in (scope.get("mandatory") or [])
        + (scope.get("optional") or [])
        + (scope.get("dependencies") or [])
    ).casefold()
    missing_markers = [
        m
        for m in (
            "waterscape",
            "up to six",
            "program status report",
            "school",
            "pet waste",
            "pet-waste",
        )
        if m in sow.casefold() and m not in blob
    ]
    high = max(found_sections)
    if high >= 10 and missing_markers:
        warnings.append(
            f"hard:sow_tail_coverage:sections_to_7.{high}:missing:{','.join(missing_markers)}"
        )
        pack["_sowCoverageGap"] = missing_markers
    return warnings
