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
from app.services.go_no_go_requirements import SUBMISSION_CATEGORY

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


def gap_matrix_from_requirements(
    requirements: list[Any],
    *,
    reason: str = "capability adjudicator unavailable — treat as unverified",
) -> list[GoNoGoCapabilityRow]:
    """Fail closed without keyword matching: every requirement starts as a gap.

    Used when the LLM adjudicator cannot run. Keyword fallbacks understate
    semantic matches (e.g. WordPress bio for CMS) and overstate false overlaps.
    """
    rows: list[GoNoGoCapabilityRow] = []
    for requirement in requirements:
        name = getattr(requirement, "requirement", "") or ""
        if not name:
            continue
        rows.append(
            GoNoGoCapabilityRow(
                requirement=name,
                status="gap",
                isCore=bool(getattr(requirement, "is_core", False)),
                disqualifying=bool(getattr(requirement, "disqualifying", False)),
                category=str(getattr(requirement, "category", "") or "service"),
                downgradeReason=reason,
                track=str(getattr(requirement, "track", "") or ""),
            )
        )
    return rows


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
        category = str(getattr(requirement, "category", "") or "service")
        disqualifying = bool(getattr(requirement, "disqualifying", False))
        track = str(getattr(requirement, "track", "") or "")
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
                    disqualifying=disqualifying,
                    category=category,
                    track=track,
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
                    disqualifying=disqualifying,
                    category=category,
                    track=track,
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
    """Core craft/platform requirements with no surviving verified evidence.

    Role/logistics cores are staffing/presence issues — they must not force the
    Technical NO-GO banner when WordPress/CMS craft is already evidenced.
    """
    return [
        row.requirement
        for row in rows
        if row.is_core
        and row.status not in {"verified", "partial"}
        and (row.category or "service").casefold() in _TECHNICAL_SCORE_CATEGORIES
    ]


# Dimensions that cannot outrun demonstrated capability.
# slack = how many points above Technical the dimension may sit.
_CAPABILITY_DEPENDENT_DIMENSIONS = {
    "win probability": 1,
    "resource availability": 1,
    "strategic value": 2,  # strategic upside can outpace weak craft only slightly
}

# Craft / platform / delivery asks drive Technical Capability.
# Pure compliance (insurance, EEO, registrations) is flagged for humans — it must
# not sit in the Technical denominator (mis-filed "municipal experience" as
# compliance previously zeroed Technical while WON City proposals were in-pool).
_TECHNICAL_SCORE_CATEGORIES = frozenset({"technical", "service"})
# Staffing titles and presence asks drive Resource Availability.
_RESOURCE_SCORE_CATEGORIES = frozenset({"role", "logistics"})


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


def resolve_headline_technical_score(rows: list[GoNoGoCapabilityRow]) -> tuple[int | None, str]:
    """Technical score for the decision matrix — per-track max when lots exist.

    Multi-track RFPs must not blend Track 1 GO with Track 2 gaps into a single
    collapsed Technical score. Headline = best calibrated track (what we can
    honestly bid); notes name every track score.
    """
    from app.models.go_no_go import GoNoGoDecisionMatrixRow  # noqa: F401 — typing only

    track_scores = per_track_technical_scores(rows)
    if track_scores:
        best_track = max(track_scores.items(), key=lambda item: item[1])
        detail = ", ".join(f"{name}={score}/5" for name, score in track_scores.items())
        note = (
            f"Technical from best bid track {best_track[0]}={best_track[1]}/5 "
            f"(per-track: {detail})."
        )
        return best_track[1], note

    score = calibrate_technical_capability_score(rows)
    if score is None:
        return None, ""
    cores = core_craft_rows(rows)
    verified = sum(1 for r in cores if r.status == "verified")
    partial = sum(1 for r in cores if r.status == "partial")
    gaps = sum(1 for r in cores if r.status not in {"verified", "partial"})
    note = (
        f"Technical from KB capability evidence: {verified} verified, "
        f"{partial} partial, {gaps} core craft gap(s) → {score}/5 "
        f"(weighted verified=full, partial=½; floors apply when proof is strong)."
    )
    return score, note


def rebuild_decision_matrix_scores(
    matrix: list[Any],
    capability_rows: list[GoNoGoCapabilityRow],
) -> tuple[list[Any], int | None]:
    """Thoroughly recalculate all five matrix cells for every RFP.

    Order (deterministic, same for every solicitation):
      1. Technical ← capability rows (calibrated; best track when multi-lot)
      2. Resource  ← role/logistics evidence when present, else analyst seed;
                     never above Technical+1; role math may only lower
      3. Financial ← analyst seed (opportunity caps applied earlier) — independent
      4. Strategic ← analyst seed, capped at Technical+2
      5. Win       ← analyst seed, capped at Technical+1; floor min(3, tech)
                     when core craft ratio ≥ 0.4 and no unmet disqualifier

    Overall Go Score = arithmetic mean of the five scores (caller averages).
    """
    from app.models.go_no_go import GoNoGoDecisionMatrixRow

    derived, tech_note = resolve_headline_technical_score(capability_rows)
    derived_resource = derive_resource_capability_score(capability_rows)
    core_gaps = unverified_core_requirements(capability_rows)
    blocked = unmet_disqualifying_requirements(capability_rows)

    out: list[GoNoGoDecisionMatrixRow] = []
    for row in matrix:
        if not isinstance(row, GoNoGoDecisionMatrixRow):
            row = GoNoGoDecisionMatrixRow.model_validate(row)
        dim = row.dimension.casefold()
        score = int(row.score)
        notes = (row.notes or "").strip()

        if dim == "technical capability match" and derived is not None:
            if score != derived:
                direction = "raised" if derived > score else "set"
                notes = (
                    f"{notes} | Score {direction} to {derived}/5 from capability "
                    f"matrix ({len(core_gaps)} core craft gap(s)). {tech_note}"
                ).strip(" |")
            else:
                notes = f"{notes} | {tech_note}".strip(" |")
            score = derived

        elif dim == "resource availability" and derived is not None:
            if derived_resource is not None and derived_resource < score:
                notes = (
                    f"{notes} | Score reduced to {derived_resource}/5 from "
                    "role/logistics requirement evidence."
                ).strip(" |")
                score = derived_resource
            cap = coherent_dimension_cap(row.dimension, derived)
            if cap is not None and score > cap:
                notes = (
                    f"{notes} | Capped at {cap}/5: cannot exceed technical "
                    f"capability ({derived}/5)."
                ).strip(" |")
                score = cap

        elif dim == "strategic value" and derived is not None:
            cap = coherent_dimension_cap(row.dimension, derived)
            if cap is not None and score > cap:
                notes = (
                    f"{notes} | Capped at {cap}/5: strategic upside limited by "
                    f"technical capability ({derived}/5)."
                ).strip(" |")
                score = cap

        elif dim == "win probability" and derived is not None:
            cap = coherent_dimension_cap(row.dimension, derived)
            if cap is not None and score > cap:
                notes = (
                    f"{notes} | Capped at {cap}/5: cannot exceed technical "
                    f"capability ({derived}/5) — {len(core_gaps)} core craft "
                    "gap(s)."
                ).strip(" |")
                score = cap

        clamped = clamp_score_to_written_cap(score, notes)
        if clamped != score:
            notes = (
                f"{notes} | Display score aligned to written cap {clamped}/5."
            ).strip(" |")
            score = clamped

        out.append(row.model_copy(update={"score": score, "notes": notes}))

    # Win floor after all caps — only when craft proof is real and responsive.
    if (
        derived is not None
        and derived >= 3
        and evidenced_core_craft_ratio(capability_rows) >= 0.4
        and not blocked
    ):
        win_floor = min(3, derived)
        raised: list[GoNoGoDecisionMatrixRow] = []
        for row in out:
            if (
                row.dimension.casefold() == "win probability"
                and row.score < win_floor
            ):
                raised.append(
                    row.model_copy(
                        update={
                            "score": win_floor,
                            "notes": (
                                f"{row.notes} | Raised to {win_floor}/5 floor: "
                                f"technical capability evidenced at {derived}/5."
                            ).strip(" |"),
                        }
                    )
                )
            else:
                raised.append(row)
        out = raised

    return out, derived


def derive_technical_capability_score(rows: list[GoNoGoCapabilityRow]) -> int | None:
    """Score 0-5 from craft/platform requirement evidence, not staffing titles.

    Role and logistics rows (assign a PM, open a CA office) are real gaps but they
    belong in Resource Availability — counting them here produced live 1/5
    Technical scores while a WordPress specialist bio was already in the KB.

    When any CORE craft rows exist, only those enter the denominator. Optional
    Task/add-on gaps (priced separately) must not zero Technical on every RFP.
    """
    if not rows:
        return None

    craft_rows = [
        row
        for row in rows
        if (row.category or "service").casefold() in _TECHNICAL_SCORE_CATEGORIES
    ]
    # Older rows without category still participate.
    pool = craft_rows or list(rows)
    core_only = [row for row in pool if row.is_core]
    scored = core_only if core_only else pool

    earned = 0.0
    possible = 0.0
    for row in scored:
        weight = 2.0 if row.is_core else 1.0
        possible += weight
        if row.status == "verified":
            earned += weight
        elif row.status == "partial":
            earned += weight * 0.5

    if possible <= 0:
        return None
    # Half-up so 2.5 (craft evidenced, infrastructure gaps) becomes 3, matching
    # human recalibration — Python round() uses bankers rounding (2.5→2).
    return max(0, min(5, int((earned / possible) * 5 + 0.5)))


def core_craft_rows(rows: list[GoNoGoCapabilityRow]) -> list[GoNoGoCapabilityRow]:
    """Core craft/platform rows — the ones Technical Capability is scored on."""
    return [
        row
        for row in rows
        if row.is_core
        and (row.category or "service").casefold() in _TECHNICAL_SCORE_CATEGORIES
    ]


def evidenced_core_craft_ratio(rows: list[GoNoGoCapabilityRow]) -> float:
    """Share of core craft rows evidenced, counting "partial" as half.

    A raw head-count ratio treats a half-credit "partial" row as full proof.
    On the Exeter run that let 10 of 24 rows (5 verified design rows + 5 thin
    partials) clear a 0.4 head-count floor and lift Technical from 2 to 3,
    while the weighted base score — and the analyst — read the same evidence as
    2/5. Floors now weight evidence exactly as ``derive_technical_capability_score``
    does, so a floor can only fire when the evidence nearly supports the score
    on its own.
    """
    cores = core_craft_rows(rows)
    if not cores:
        return 0.0
    earned = sum(
        1.0 if row.status == "verified" else 0.5 if row.status == "partial" else 0.0
        for row in cores
    )
    return earned / len(cores)


def unmet_disqualifying_requirements(rows: list[GoNoGoCapabilityRow]) -> list[str]:
    """Stated minimum thresholds the KB cannot satisfy.

    These are pass/fail responsiveness gates ("at least five comparable
    California municipal projects in the past five years"), not scored rows.
    Averaging one into a matrix produced a 2.8/5 "GO WITH CONDITIONS" on an RFP
    the agency could not answer without inventing case studies. An unmet
    disqualifier ends the pursuit regardless of what the other dimensions say.

    Proposal-content rows are excluded no matter how they are flagged. "Provide
    three client references" is a form every bidder can fill out; it is not a
    threshold zö can fail. On the Gilroy run two such rows arrived
    disqualifying=true, forced NO-GO outright, and suppressed every calibration
    floor while the scope itself matched a won proposal already in the KB.
    """
    return [
        row.requirement
        for row in rows
        if getattr(row, "disqualifying", False)
        and row.status not in {"verified", "partial"}
        and (row.category or "service").casefold() != SUBMISSION_CATEGORY
    ]


def calibrate_technical_capability_score(rows: list[GoNoGoCapabilityRow]) -> int | None:
    """Raise understated technical scores when KB proof is strong but uneven.

    Raw ratio scoring can land at 2/5 when several core craft rows are verified
    (campaign case studies, media bios) while evaluation-only sub-asks stay gap.
    The rubric calls for ~3/5 when craft is evidenced with real gaps, and ~4/5
    when multiple delivery rows align with the RFP's core scope.

    Floors never fire while a stated minimum threshold is unmet — the agency
    cannot be scored competent at scope it is not responsive for.
    """
    base = derive_technical_capability_score(rows)
    if base is None:
        return None

    craft_cores = core_craft_rows(rows)
    if not craft_cores:
        return base
    if unmet_disqualifying_requirements(rows):
        return base

    verified = sum(1 for row in craft_cores if row.status == "verified")
    partial = sum(1 for row in craft_cores if row.status == "partial")
    evidenced_n = verified + partial
    ratio = evidenced_core_craft_ratio(rows)

    service_evidenced = sum(
        1
        for row in craft_cores
        if row.status in {"verified", "partial"}
        and (row.category or "service").casefold() in {"service", "technical"}
    )

    floor = base
    # Partials count: won/CS craft family proof often lands partial when the
    # RFP lists tactics the case study does not name verbatim.
    if ratio >= 0.25 and evidenced_n >= 2:
        floor = max(floor, 3)
    if ratio >= 0.4 and verified >= 2:
        floor = max(floor, 3)
    if service_evidenced >= 2 and ratio >= 0.3:
        floor = max(floor, 3)
    if ratio >= 0.55 and verified >= 3:
        floor = max(floor, 4)
    if service_evidenced >= 3 and ratio >= 0.45:
        floor = max(floor, 4)

    # Multiple missing core crafts cannot be a 4. A live run scored Technical
    # 4/5 while the notes said three material gaps "prevent a 4/5".
    core_craft_gaps = sum(
        1 for row in craft_cores if row.status not in {"verified", "partial"}
    )
    if core_craft_gaps >= 3:
        floor = min(floor, 3)

    return max(0, min(5, floor))


_WRITTEN_CAP_RE = re.compile(r"capped at\s+(\d)\s*/\s*5", re.IGNORECASE)


def clamp_score_to_written_cap(score: int, notes: str) -> int:
    """If notes say 'capped at N/5', the displayed score cannot exceed N.

    Live runs showed Resource Availability 4/5 while the same cell said
    'capped at 2/5'. The written cap is the intended score.
    """
    match = _WRITTEN_CAP_RE.search(notes or "")
    if not match:
        return score
    cap = int(match.group(1))
    return max(0, min(score, cap))


def derive_resource_capability_score(rows: list[GoNoGoCapabilityRow]) -> int | None:
    """Score 0-5 from role / logistics evidence (staffing, presence, assignments)."""
    if not rows:
        return None
    staff_rows = [
        row
        for row in rows
        if (row.category or "").casefold() in _RESOURCE_SCORE_CATEGORIES
    ]
    if not staff_rows:
        return None

    earned = 0.0
    possible = 0.0
    for row in staff_rows:
        weight = 2.0 if row.is_core else 1.0
        possible += weight
        if row.status == "verified":
            earned += weight
        elif row.status == "partial":
            earned += weight * 0.5
    if possible <= 0:
        return None
    return max(0, min(5, int((earned / possible) * 5 + 0.5)))


def tracks_in_rows(rows: list[GoNoGoCapabilityRow]) -> list[str]:
    """Distinct non-empty track labels, in first-seen order.

    Empty when the RFP is single-scope (every row's track is "") — callers use
    this to detect whether per-track segmentation applies at all.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in rows:
        track = (row.track or "").strip()
        if not track or track in seen_set:
            continue
        seen_set.add(track)
        seen.append(track)
    return seen


def rows_for_track(rows: list[GoNoGoCapabilityRow], track: str) -> list[GoNoGoCapabilityRow]:
    """Rows for one track PLUS the track-agnostic rows (track == "").

    Compliance/insurance/reference requirements apply to every track, so they
    belong in each track's denominator; scoping them out would make a track
    look artificially clean.
    """
    return [row for row in rows if (row.track or "") in {"", track}]


def per_track_technical_scores(rows: list[GoNoGoCapabilityRow]) -> dict[str, int]:
    """``calibrate_technical_capability_score`` run per track.

    Empty dict when the RFP is single-scope, so callers keep today's behaviour.
    """
    tracks = tracks_in_rows(rows)
    scores: dict[str, int] = {}
    for track in tracks:
        score = calibrate_technical_capability_score(rows_for_track(rows, track))
        if score is not None:
            scores[track] = score
    return scores


def per_track_resource_scores(rows: list[GoNoGoCapabilityRow]) -> dict[str, int]:
    """``derive_resource_capability_score`` run per track.

    Empty dict when the RFP is single-scope, so callers keep today's behaviour.
    """
    tracks = tracks_in_rows(rows)
    scores: dict[str, int] = {}
    for track in tracks:
        score = derive_resource_capability_score(rows_for_track(rows, track))
        if score is not None:
            scores[track] = score
    return scores
