"""Deterministic validation of Go/No-Go capability claims against retrieved KB.

The Go/No-Go tool used to assert "Verified" for capabilities with no supporting
KB document at all — CMS implementation, hosting, content migration and
municipal website redesign were all reported Verified for an RFP whose KB
contained none of them. Those claims were free-form Markdown, so no code could
check them, and they propped up the Technical Capability score that produced a
"GO WITH CONDITIONS".

Validation here is set-membership plus term presence, never model judgment:

  a row keeps status="verified" only if
     (1) kb_source names a document actually retrieved for THIS RFP, and
     (2) that document's text contains the distinctive terms of the requirement.

(2) is what separates "content development" from "content migration" — a real
document cited for a capability it does not evidence.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from app.models.go_no_go import GoNoGoCapabilityRow

logger = logging.getLogger(__name__)

# Words too common to prove a capability match on their own.
_STOPWORDS = frozenset(
    {
        "and", "or", "the", "a", "an", "of", "for", "to", "in", "on", "with",
        "by", "from", "at", "as", "is", "are", "be", "been", "will", "shall",
        "must", "should", "may", "can", "we", "our", "their", "its", "this",
        "that", "these", "those", "all", "any", "each", "other", "including",
        "include", "provide", "support", "service", "services", "experience",
        "capability", "capabilities", "solution", "solutions", "approach",
        "management", "system", "systems", "project", "work", "new", "per",
        "rfp", "proposal", "vendor", "offeror", "agency", "client", "team",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Longest-first so "development" strips "ment" before "ed"/"er".
_SUFFIXES = (
    "ization", "ational", "iveness", "fulness", "ousness",
    "ation", "ition", "ement", "ingly", "edly",
    "ment", "ness", "ions", "ing", "ies", "ied", "est", "ed", "er", "ly",
)

# Share of a requirement's distinctive terms that must appear in the cited
# document. Short requirements need all of them: "content migration" against a
# doc that only says "content development" would otherwise pass on one word,
# which is exactly the conflation this validator exists to catch.
_TERM_COVERAGE_THRESHOLD = 0.6
_SHORT_REQUIREMENT_TERMS = 2

# Domain synonyms so Maricopa-style "Television / Broadcast / TV / video"
# evidences RFP language like "broadcast production and multimedia editing"
# without loosening unrelated term matches (coverage still requires most terms).
_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "broadcast",
            "television",
            "tv",
            "radio",
            "video",
            "cinema",
            "multimedia",
            "media",
        }
    ),
    frozenset({"edit", "produce", "production", "produc"}),
    # "Plan and buy traditional media" evidences "media buying / negotiation".
    frozenset({"buy", "buying", "purchase", "purchas", "placement", "negoti"}),
)


def _synonym_aliases(token: str) -> set[str]:
    for group in _SYNONYM_GROUPS:
        if token in group:
            return set(group)
    return {token}


def _stem(token: str) -> str:
    """Crude suffix stripper so websites/website and developer/development match."""
    word = token
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-2] if word.endswith("es") and len(word) > 4 else word[:-1]
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    return [
        _stem(t)
        for t in _TOKEN_RE.findall((text or "").casefold())
        if t not in _STOPWORDS
    ]


def _normalize_source(name: str) -> str:
    """Normalize a document name for comparison (case, path, extension)."""
    base = (name or "").strip().casefold()
    base = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = re.sub(r"\.(pdf|docx?|txt|md|pptx?|xlsx?)$", "", base)
    return re.sub(r"[^a-z0-9]+", "", base)


def build_source_index(hits: Iterable[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Map normalized document name -> (display name, retrieved text).

    Built from the hits actually returned for this RFP, so a citation to a
    document that was never retrieved cannot validate. The display name is kept
    alongside because the normalized key ("04bioshawndicriscio") is a lookup
    token, not something to show a reader.
    """
    from app.services import supermemory

    index: dict[str, tuple[str, str]] = {}
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        name = ""
        try:
            name = supermemory.hit_file_name(hit) or ""
        except Exception:  # pragma: no cover - defensive around hit shape
            name = ""
        name = name or str(hit.get("title") or hit.get("id") or "")
        key = _normalize_source(name)
        if not key:
            continue
        try:
            text = supermemory.hit_text(hit) or ""
        except Exception:  # pragma: no cover
            text = str(hit.get("content") or "")
        display, existing = index.get(key, (name, ""))
        index[key] = (display or name, (existing + "\n" + text).strip())
    return index


def _find_source_text(
    kb_source: str, index: dict[str, tuple[str, str]]
) -> str | None:
    """Return the retrieved text for a citation, or None when not retrieved."""
    key = _normalize_source(kb_source)
    if not key:
        return None
    if key in index:
        return index[key][1]
    # A citation may name the document loosely ("03_CS City of Bend branding").
    for indexed_key, (_display, text) in index.items():
        if key in indexed_key or indexed_key in key:
            return text
    return None


# KB text often qualifies a skill claim rather than asserting it — the master
# bio for a Creative Director reads "Web Design/Development (Not Programming)".
# Bare term matching reads that as evidence OF development. Terms appearing only
# inside a disclaimer must not count.
_NEGATION_RE = re.compile(
    r"(?i)\b(?:not|non|no|without|excluding|excludes|except|rather\s+than|"
    r"does\s+not|doesn't|never|minus|other\s+than)\b"
)
# A disclaimer scopes to its own clause, never past it. "(Not Programming) -
# 15 years. Print and brand graphic design." must disclaim only "Programming";
# a fixed-width window swallowed the unrelated design skills that followed.
_CLAUSE_END_RE = re.compile(r"[.;:)\]\n|]|\s-\s|—")
_NEGATION_MAX_SCOPE = 60


def _negated_spans(text: str) -> list[tuple[int, int]]:
    body = text or ""
    spans: list[tuple[int, int]] = []
    for match in _NEGATION_RE.finditer(body):
        hard_limit = min(len(body), match.end() + _NEGATION_MAX_SCOPE)
        clause = _CLAUSE_END_RE.search(body, match.end(), hard_limit)
        spans.append((match.start(), clause.start() if clause else hard_limit))
    return spans


def _affirmative_tokens(text: str) -> set[str]:
    """Stemmed tokens from ``text`` excluding those inside a disclaimer."""
    spans = _negated_spans(text)
    if not spans:
        return set(_tokens(text))

    kept: list[str] = []
    for match in _TOKEN_RE.finditer((text or "").casefold()):
        if any(start <= match.start() < end for start, end in spans):
            continue
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        kept.append(_stem(token))
    return set(kept)


def _source_supports(requirement: str, source_text: str) -> bool:
    """True when the cited document actually evidences the requirement."""
    terms = set(_tokens(requirement))
    if not terms:
        return False
    haystack = _affirmative_tokens(source_text)
    matched = 0
    for term in terms:
        if _synonym_aliases(term) & haystack:
            matched += 1
    coverage = matched / len(terms)
    if len(terms) <= _SHORT_REQUIREMENT_TERMS:
        return coverage >= 1.0
    return coverage >= _TERM_COVERAGE_THRESHOLD


def validate_capability_rows(
    rows: list[GoNoGoCapabilityRow],
    hits: Iterable[dict[str, Any]],
) -> tuple[list[GoNoGoCapabilityRow], list[str]]:
    """Downgrade capability claims that retrieved KB evidence does not support.

    Returns (validated_rows, downgrade_messages).
    """
    index = build_source_index(hits)
    out: list[GoNoGoCapabilityRow] = []
    messages: list[str] = []

    for row in rows:
        if row.status not in {"verified", "partial"}:
            out.append(row)
            continue

        if not row.kb_source.strip():
            reason = "no KB source cited"
        else:
            source_text = _find_source_text(row.kb_source, index)
            if source_text is None:
                reason = f"cited source '{row.kb_source}' was not retrieved from the KB"
            elif not _source_supports(row.requirement, source_text):
                reason = (
                    f"cited source '{row.kb_source}' does not evidence "
                    f"'{row.requirement}'"
                )
            else:
                out.append(row)
                continue

        messages.append(f"{row.requirement}: {reason}")
        out.append(
            row.model_copy(
                update={
                    "status": "unverified",
                    "downgrade_reason": reason,
                }
            )
        )

    if messages:
        logger.info(
            "go_no_go capability downgrades=%d: %s",
            len(messages),
            "; ".join(messages[:10]),
        )
    return out, messages


# Multi-word verdicts first so "GO WITH CONDITIONS" is not partially matched.
_INLINE_VERDICT_RE = re.compile(r"(?i)\b(?:GO\s+WITH\s+CONDITIONS|NO[\s-]?GO)\b")
# Bare "GO" only when written as a standalone uppercase verdict — never the
# word "Go" inside prose such as "Overall Go Score".
_BARE_GO_RE = re.compile(r"\bGO\b(?!\s*(?:[Ss]core|/|\s*[Ww]ith))")
_STATED_SCORE_RE = re.compile(
    r"(?i)\b(?:overall\s+(?:go\s+)?score|go\s+score)\s*[:\-—]?\s*\d+(?:\.\d+)?\s*/\s*5"
)
_SCORE_PLACEHOLDER = "\x00SCORE\x00"
# Verdicts are parked too: substituting the label directly let the bare-GO pass
# re-match the "GO" inside a just-written "NO-GO", yielding "NO-NO-GO".
_VERDICT_PLACEHOLDER = "\x00VERDICT\x00"

_VERDICT_LABEL = {
    "go": "GO",
    "no_go": "NO-GO",
    "review": "GO WITH CONDITIONS",
}


def reconcile_narrative(
    report: str,
    *,
    recommendation: str | None,
    overall_score: float | None,
) -> str:
    """Rewrite verdict/score claims in the narrative to match the enforced result.

    The model writes its own verdict and score into stageOneReport. Enforcement
    previously changed only the structured fields, so a report could read
    "GO WITH CONDITIONS ... Overall Go Score 3.4/5" directly above a list of
    core requirements it had just admitted were unevidenced, while the
    structured recommendation said no_go. Readers act on the prose, so the
    prose has to agree with the verdict.
    """
    if not report or not recommendation:
        return report

    label = _VERDICT_LABEL.get(recommendation)
    if not label:
        return report

    # Park score claims first so verdict rewriting cannot eat them, then put
    # the corrected score back.
    out = _STATED_SCORE_RE.sub(_SCORE_PLACEHOLDER, report)
    out = _INLINE_VERDICT_RE.sub(_VERDICT_PLACEHOLDER, out)
    out = _BARE_GO_RE.sub(_VERDICT_PLACEHOLDER, out)

    replacement = (
        f"Overall Go Score {overall_score}/5"
        if overall_score is not None
        else "Overall Go Score not available"
    )
    out = out.replace(_SCORE_PLACEHOLDER, replacement)
    return out.replace(_VERDICT_PLACEHOLDER, label)


def build_matrix_from_requirements(
    requirements: list[Any],
    hits_by_requirement: dict[str, list[dict[str, Any]]],
) -> list[GoNoGoCapabilityRow]:
    """Build the capability matrix from RFP requirements and their own evidence.

    Every requirement starts as a gap and is upgraded only by a retrieved
    document whose text supports it. The model never writes this matrix, so it
    cannot omit a requirement it has no evidence for nor assert one it does —
    the two failure modes that produced fabricated "Verified" rows.
    """
    rows: list[GoNoGoCapabilityRow] = []

    for requirement in requirements:
        name = getattr(requirement, "requirement", "") or ""
        if not name:
            continue
        is_core = bool(getattr(requirement, "is_core", False))
        hits = hits_by_requirement.get(name, [])
        index = build_source_index(hits)

        best_source = ""
        best_evidence = ""
        for _key, (display_name, text) in index.items():
            if _source_supports(name, text):
                best_source = display_name
                best_evidence = text[:400]
                break

        if best_source:
            rows.append(
                GoNoGoCapabilityRow(
                    requirement=name,
                    status="verified",
                    kbSource=best_source,
                    evidence=best_evidence,
                    isCore=is_core,
                )
            )
        else:
            rows.append(
                GoNoGoCapabilityRow(
                    requirement=name,
                    status="gap",
                    kbSource="",
                    evidence="",
                    isCore=is_core,
                    downgradeReason=(
                        "no retrieved KB document evidences this requirement"
                        if hits
                        else "no KB results returned for this requirement"
                    ),
                )
            )

    logger.info(
        "go_no_go matrix built from requirements: %d rows, %d verified, %d core gaps",
        len(rows),
        sum(1 for r in rows if r.status == "verified"),
        sum(1 for r in rows if r.is_core and r.status != "verified"),
    )
    return rows


_STATUS_DISPLAY = {
    "verified": "✅ Verified",
    "partial": "◐ Partial",
    "gap": "❌ Gap",
    "unverified": "⚠️ Unverified",
}

_CAPABILITY_HEADING = "## CAPABILITY ASSESSMENT"

# Any heading whose title looks like a capability/requirement-coverage section.
# The model writes several — "CAPABILITY ASSESSMENT", "Technical and Service
# Requirements vs. zö Capabilities", "Required Industry Experience vs.
# Documented Experience". A live run kept a second, unvalidated table asserting
# "CMS implementation — Shawn DiCrisio — Verified" directly beneath the
# validated table marking that same row a Gap. Every such section is removed and
# replaced by the one validated table.
_CAPABILITY_SECTION_RE = re.compile(
    r"(?im)^#{1,6}[^\n]*?"
    r"(?:capabilit|requirements?\s+vs\.?|experience\s+vs\.?|"
    r"requirement\s+coverage|yes\s*/?\s*gap)"
    r"[^\n]*\n.*?"
    r"(?=^#{1,6}\s|\Z)",
    re.S | re.M,
)


def render_capability_table(rows: list[GoNoGoCapabilityRow]) -> str:
    """Markdown section showing each RFP requirement and its evidence.

    Rendered from the validated rows so the reader sees exactly what was
    checked, which document backed it, and — when it did not hold up — why.
    """
    if not rows:
        return ""

    lines = [
        _CAPABILITY_HEADING,
        "",
        "Each requirement below was matched against the knowledge-base "
        "documents actually retrieved for it. A requirement is Verified only "
        "when a retrieved document evidences it.",
        "",
        "| RFP Requirement | Core | Status | KB Evidence / Why Not |",
        "| --- | --- | --- | --- |",
    ]

    for row in rows:
        status = _STATUS_DISPLAY.get(row.status, row.status)
        core = "Yes" if row.is_core else "—"
        if row.status in {"verified", "partial"} and row.kb_source:
            detail = row.kb_source
        elif row.downgrade_reason:
            detail = row.downgrade_reason
        else:
            detail = "no supporting KB document"
        # "Nothing in the KB" and "the KB says otherwise" are different
        # findings: the first may be a re-ingestion problem, the second never is.
        state = {
            "absent": "not in KB",
            "contradicted": "KB contradicts",
            "adjacent": "related work only",
        }.get(row.evidence_state)
        if state and row.status == "gap":
            detail = f"({state}) {detail}"
        cell = lambda text: str(text).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {cell(row.requirement)} | {core} | {status} | {cell(detail)} |"
        )

    verified = sum(1 for r in rows if r.status == "verified")
    core_gaps = sum(1 for r in rows if r.is_core and r.status != "verified")
    lines += [
        "",
        f"**{verified} of {len(rows)} requirements evidenced.** "
        f"{core_gaps} core requirement(s) lack verifiable KB evidence.",
        "",
    ]
    return "\n".join(lines)


def upsert_capability_section(report: str, rows: list[GoNoGoCapabilityRow]) -> str:
    """Put the validated capability table into the report, replacing any other.

    The model writes its own capability table in the narrative. Leaving it in
    place next to the validated one gives the reader two tables disagreeing
    about the same requirements, so the model's version is replaced outright.
    """
    table = render_capability_table(rows)
    if not table:
        return report
    if not report.strip():
        return table

    if _CAPABILITY_SECTION_RE.search(report):
        # Remove every capability-style section, then insert the validated table
        # where the first one stood. Replacing only the first left later,
        # unvalidated tables contradicting it.
        first = True
        replaced: list[str] = []

        def _swap(match: re.Match[str]) -> str:
            nonlocal first
            if first:
                first = False
                return table + "\n"
            replaced.append(match.group(0)[:60])
            return ""

        out = _CAPABILITY_SECTION_RE.sub(_swap, report)
        if replaced:
            logger.info(
                "go_no_go removed %d unvalidated capability section(s) from report",
                len(replaced),
            )
        return out
    return f"{report.rstrip()}\n\n{table}"


def unverified_core_requirements(rows: list[GoNoGoCapabilityRow]) -> list[str]:
    """Core requirements with no surviving verified evidence."""
    return [
        row.requirement
        for row in rows
        if row.is_core and row.status not in {"verified", "partial"}
    ]


# Dimensions that cannot outrun demonstrated capability.
#
# Observed: Technical Capability Match 0/5 (20 core requirements unevidenced)
# sitting beside Win Probability 4/5 and Resource Availability 4/5, averaging
# to a 3.0 "moderate" verdict. You cannot be likely to win, or be staffed for,
# work you cannot evidence having done. Financial Viability (is the money
# worthwhile) and Strategic Value (is the win worth having) are genuinely
# independent of whether zö can deliver, so they are left alone.
_CAPABILITY_DEPENDENT_DIMENSIONS = {
    "win probability": 1,
    "resource availability": 1,
}


def coherent_dimension_cap(dimension: str, technical_score: int | None) -> int | None:
    """Highest score ``dimension`` may hold given the technical capability score.

    Returns None when the dimension is independent of capability.
    """
    if technical_score is None:
        return None
    slack = _CAPABILITY_DEPENDENT_DIMENSIONS.get((dimension or "").strip().casefold())
    if slack is None:
        return None
    return max(0, min(5, technical_score + slack))


def derive_technical_capability_score(rows: list[GoNoGoCapabilityRow]) -> int | None:
    """Score 0-5 from the verified share of requirements, not model opinion.

    Core requirements count double: an RFP whose load-bearing asks are
    unevidenced is a poor technical match however many peripheral rows match.
    """
    if not rows:
        return None

    earned = 0.0
    possible = 0.0
    for row in rows:
        weight = 2.0 if row.is_core else 1.0
        possible += weight
        if row.status == "verified":
            earned += weight
        elif row.status == "partial":
            earned += weight * 0.5

    if possible <= 0:
        return None
    return max(0, min(5, round((earned / possible) * 5)))
