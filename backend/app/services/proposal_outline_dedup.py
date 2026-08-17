"""Lean outline hygiene — enrich boring titles; keep important + closing RFP tabs."""

from __future__ import annotations

import re
from typing import Any

from app.services.proposal_voice_enforcement import (
    is_duplicate_static_rfp_section,
    should_skip_rfp_section_as_static_duplicate,
)

# Short invented marketing labels — enrich from RFP when possible; drop only if
# the RFP never mentions them AND they are not closing/important submission items.
_GENERIC_FILLER_TITLES = re.compile(
    r"^(?:"
    r"executive\s+summary|"
    r"introduction|"
    r"overview|"
    r"price|"
    r"pricing|"
    r"budget|"
    r"fees|"
    r"cost|"
    r"understanding(?:\s+of\s+the\s+(?:project|rfp|scope))?|"
    r"why\s+(?:us|zö|zo)|"
    r"about\s+us|"
    r"our\s+approach|"
    r"methodology|"
    r"timeline|"
    r"project\s+plan|"
    r"technical\s+ability"
    r")$",
    re.IGNORECASE,
)

_IMPORTANT_OR_CLOSING_TITLE_RE = re.compile(
    r"\b("
    r"reference|"
    r"addenda|addendum|"
    r"non[\s-]*collusion|"
    r"ownership\s+disclosure|"
    r"authorized\s+signature|"
    r"pricing\s+proposal\s+form|quotation\s+form|fee\s+schedule|cost\s+proposal|"
    r"closing\s+statement|offeror\s+commitment|proposer\s+commitment|"
    r"sample\s+work|portfolio|"
    r"agency\s+requirements?|capability\s+matrix|"
    r"scope\s+of\s+work|statement\s+of\s+work|"
    r"insurance|certificate\s+of\s+insurance|w-?9|"
    r"evaluation|technical\s+approach|public\s+awareness"
    r")\b",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "section",
    "of",
    "to",
    "a",
    "an",
    "or",
    "in",
    "on",
    "per",
    "rfp",
}


def normalize_outline_title(title: str) -> str:
    text = (title or "").strip().casefold()
    text = re.sub(r"^\s*(?:rfp[-\s]?sec(?:tion)?[-\s]?\d+\s*[—\-–:]?\s*)", "", text)
    text = re.sub(r"^\s*\d+(?:\.\d+)*\s*[—\-–:.]?\s*", "", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def outline_title_tokens(title: str) -> set[str]:
    return {
        t
        for t in normalize_outline_title(title).split()
        if len(t) >= 3 and t not in _STOPWORDS
    }


def outline_title_head_label(title: str) -> str:
    """Structural head before — / : / |, ignoring parentheticals.

    Free, RFP-agnostic sibling detector: "Budget — Narrative" and
    "Budget — Disbursement" share head "budget"; "Cover Letter (1 page)" and
    "Cover Letter — Contact Info" share "cover letter". No topic word lists.
    """
    text = (title or "").strip()
    if not text:
        return ""
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.split(r"\s*[—–|:]\s*", text, maxsplit=1)[0]
    return normalize_outline_title(text)


def outline_titles_near_duplicate(a: str, b: str, *, threshold: float = 0.72) -> bool:
    """True when two outline titles likely cover the same ask."""
    na = normalize_outline_title(a)
    nb = normalize_outline_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Bare Price vs Proposal Pricing / Fee Schedule / Hourly Rates — same ask.
    if is_pricing_outline_title(a) and is_pricing_outline_title(b):
        return True
    # Agency Requirements G.# siblings are collapsed later in
    # collapse_agency_requirements_siblings (need all titles to build G.1–G.16 span).
    if is_agency_requirements_title(a) and is_agency_requirements_title(b):
        return False
    # Same structural head ("Specific Experience — …" twins) — any RFP wording.
    ha, hb = outline_title_head_label(a), outline_title_head_label(b)
    if ha and hb and ha == hb:
        a_sep = bool(re.search(r"[—–|:]", a or ""))
        b_sep = bool(re.search(r"[—–|:]", b or ""))
        # Two "Head — variant" siblings, or a multi-word head shared after
        # stripping parentheticals (Cover Letter (1 page) vs Cover Letter — …).
        if (a_sep and b_sep) or len(ha.split()) >= 2:
            return True
    # "References — three contacts" vs "References & Past Performance":
    # shorter head is a token prefix of the longer head (same opening ask).
    if ha and hb:
        ha_toks, hb_toks = ha.split(), hb.split()
        if ha_toks and hb_toks:
            shorter, longer = (
                (ha_toks, hb_toks)
                if len(ha_toks) <= len(hb_toks)
                else (hb_toks, ha_toks)
            )
            if longer[: len(shorter)] == shorter:
                return True
    if na in nb or nb in na:
        return True
    ta, tb = outline_title_tokens(a), outline_title_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    if union <= 0:
        return False
    jaccard = inter / union
    coverage = inter / min(len(ta), len(tb))
    return jaccard >= threshold or coverage >= 0.85


_AGENCY_REQ_TITLE_RE = re.compile(
    r"\bagency\s+requirements?\b|\bcapability\s+matrix\b|\bG\.\s*\d+\b",
    re.IGNORECASE,
)
_PRICING_TITLE_RE = re.compile(
    r"\b("
    r"price|pricing|fee\s+schedule|cost\s+proposal|quotation\s+form|"
    r"hourly\s+rates?|labor\s+categor"
    r")\b",
    re.IGNORECASE,
)


def is_pricing_outline_title(title: str) -> bool:
    return bool(_PRICING_TITLE_RE.search(title or ""))


_G_CODE_RE = re.compile(r"\bG\.\s*(\d+)\b", re.IGNORECASE)


def is_agency_requirements_title(title: str) -> bool:
    return bool(_AGENCY_REQ_TITLE_RE.search(title or ""))


def _agency_req_g_codes(title: str) -> list[int]:
    return [int(n) for n in _G_CODE_RE.findall(title or "")]


def build_merged_agency_requirements_title(titles: list[str]) -> str:
    """One concrete matrix title covering every G.# sibling that was collapsed."""
    codes: list[int] = []
    for title in titles:
        codes.extend(_agency_req_g_codes(title))
    codes = sorted(set(codes))
    if codes:
        lo, hi = codes[0], codes[-1]
        span = f"G.{lo}–G.{hi}" if lo != hi else f"G.{lo}"
        return (
            f"Agency Requirements — Capability Matrix ({span}; "
            "Section III A services covered in one response)"
        )
    return (
        "Agency Requirements — Capability Matrix "
        "(Section III A services covered in one response)"
    )


def collapse_agency_requirements_siblings(
    sections: list[Any],
) -> tuple[list[Any], list[str]]:
    """Merge many Agency Requirements — G.# tabs into a single matrix section."""
    dropped: list[str] = []
    agency_idxs: list[int] = []
    titles: list[str] = []

    def _title(section: Any) -> str:
        if hasattr(section, "title"):
            return str(section.title or "")
        if isinstance(section, dict):
            return str(section.get("title") or "")
        return ""

    def _set_title(section: Any, title: str) -> None:
        if hasattr(section, "title"):
            section.title = title
        elif isinstance(section, dict):
            section["title"] = title

    for i, section in enumerate(sections):
        title = _title(section)
        if is_agency_requirements_title(title):
            agency_idxs.append(i)
            titles.append(title)

    if len(agency_idxs) <= 1:
        return sections, dropped

    keep_i = agency_idxs[0]
    merged_title = build_merged_agency_requirements_title(titles)
    _set_title(sections[keep_i], merged_title)
    drop_set = set(agency_idxs[1:])
    for i in agency_idxs[1:]:
        dropped.append(f"{_title(sections[i])} (merged into Agency Requirements matrix)")
    out = [s for i, s in enumerate(sections) if i not in drop_set]
    return out, dropped


# KB filenames sometimes reach the outline as section titles — a live draft
# produced a tab literally called "3.2 — Copy of 03 CS All Case Studies Last
# Updated". These are storage artefacts, never headings a buyer should read.
_KB_ARTEFACT_TITLE_RE = re.compile(
    r"(?:"
    r"\bcopy\s+of\b|"
    # "03_CS_Torrent" — \b fails after CS because "_" is a word character.
    r"\b0\d[_\s-]*(?:cs|won|fin|bio|guide|companyfacts|clientlist|mastertemplate)"
    r"(?![a-z])|"
    r"\.(?:pdf|docx?|xlsx?|pptx?)(?![a-z])|"
    r"\blast\s+updated\b|"
    r"\bfinal[_\s-]*v\d|"
    r"\buntitled\b"
    r")",
    re.IGNORECASE,
)


def is_kb_artefact_outline_title(title: str) -> bool:
    """True when a section title is really a knowledge-base filename."""
    return bool(_KB_ARTEFACT_TITLE_RE.search(title or ""))


def is_important_or_closing_outline_title(title: str) -> bool:
    """Submission-critical / scored / closing tabs — never drop for being 'generic'."""
    return bool(_IMPORTANT_OR_CLOSING_TITLE_RE.search(title or ""))


def _section_evaluation_points(section: Any) -> float | None:
    """Read evaluation points off a section regardless of its shape.

    Some pipelines pass Pydantic ``OutlineSection`` objects (no points field
    yet), others pass ``RfpSectionMap``-derived dicts carrying
    ``evaluationWeight`` once Phase 2 has scored the section —
    ``filter_lean_outline_sections`` runs on both.
    """
    for attr, key in (("evaluation_weight", "evaluationWeight"), ("points", "points")):
        if hasattr(section, attr):
            value = getattr(section, attr)
            if value is not None:
                return value
        if isinstance(section, dict):
            value = section.get(key)
            if value is None:
                value = section.get(attr)
            if value is not None:
                return value
    return None


def section_carries_evaluation_points(section: Any) -> bool:
    """True when a section is tied to a scored RFP criterion.

    Observed: an RFP named "Technical Approach" a 30-point scored criterion;
    the outline planner's own prompt forbids inventing an "Approach" tab, and
    _GENERIC_FILLER_TITLES matches ``our approach|methodology`` — so the
    section was silently dropped. Anti-boilerplate rules exist for invented
    marketing labels, never for a criterion an evaluator scores.
    """
    points = _section_evaluation_points(section)
    try:
        return points is not None and float(points) > 0
    except (TypeError, ValueError):
        return False


_TOC_HEADING_LINE_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)*[.)\s]+|"  # 1. / 4.2 /
    r"[A-Z][.)]\s+|"  # A. /
    r"[IVXLC]+\.\s+|"  # IV.
    r"[-•*]\s+"
    r")?"
    r"(.{3,140})$"
)


def rfp_lists_section_heading(rfp_text: str, title: str) -> bool:
    """True when the RFP TOC / proposal-contents list names this heading.

    Unlike ``rfp_requires_topic``, this does NOT need nearby "shall submit"
    verbs — bare TOC labels ("2. Technical Approach") are RFP-demanded tabs.
    """
    core = normalize_outline_title(title)
    if not core or not (rfp_text or "").strip():
        return False
    core_tokens = {t for t in outline_title_tokens(title) if len(t) >= 4}
    for raw_line in (rfp_text or "").splitlines():
        line = raw_line.strip().strip("•*-–—")
        if len(line) < 3 or len(line) > 160:
            continue
        m = _TOC_HEADING_LINE_RE.match(line)
        if not m:
            continue
        heading = m.group(1).strip()
        # Skip long prose sentences posing as headings
        if heading.count(" ") > 16 or heading.endswith((".", ";", ",")):
            # Allow short title-case endings without period requirement
            if heading.endswith((".", ";", ",")) and len(heading.split()) > 8:
                continue
        norm = normalize_outline_title(heading)
        if not norm:
            continue
        if core == norm or core in norm or norm in core:
            return True
        if core_tokens:
            line_tokens = {t for t in outline_title_tokens(heading) if len(t) >= 4}
            if len(core_tokens & line_tokens) >= max(1, min(2, len(core_tokens))):
                # Prefer real TOC-ish lines (numbered / short)
                numbered = bool(re.match(r"^\d+(?:\.\d+)*[.)\s]+", line))
                short = len(line_tokens) <= 10
                if numbered or short:
                    return True
    return False


def _set_section_evaluation_weight(section: Any, weight: float) -> None:
    if hasattr(section, "evaluation_weight"):
        section.evaluation_weight = weight
    elif isinstance(section, dict):
        section["evaluationWeight"] = weight


def stamp_outline_evaluation_weights(
    sections: list[Any],
    criteria: list[Any],
) -> list[Any]:
    """Copy evaluation criterion points onto matching outline sections.

    Must run BEFORE ``filter_lean_outline_sections`` so scored carve-outs work
    on live planner output (OutlineSection historically had no weight field).
    """
    crit_rows: list[tuple[str, float]] = []
    for crit in criteria or []:
        if hasattr(crit, "name"):
            name = str(crit.name or "").strip()
            weight = crit.weight
        elif isinstance(crit, dict):
            name = str(crit.get("name") or "").strip()
            weight = crit.get("weight")
        else:
            continue
        if not name or weight is None:
            continue
        try:
            pts = float(weight)
        except (TypeError, ValueError):
            continue
        if pts <= 0:
            continue
        crit_rows.append((name, pts))

    for section in sections:
        if section_carries_evaluation_points(section):
            continue
        title = ""
        if hasattr(section, "title"):
            title = str(section.title or "")
        elif isinstance(section, dict):
            title = str(section.get("title") or "")
        title_cf = title.casefold()
        title_tokens = {t for t in outline_title_tokens(title) if len(t) >= 4}
        best: float | None = None
        best_score = 0
        for name, pts in crit_rows:
            name_cf = name.casefold()
            # Exact / containment match only — a single shared token (e.g. "Experience")
            # used to stamp every related tab as "scored", which blocked lean drops and
            # ballooned outlines past 30 tabs.
            if name_cf == title_cf or name_cf in title_cf or title_cf in name_cf:
                best = pts
                best_score = 100
                break
            name_tokens = {t for t in outline_title_tokens(name) if len(t) >= 4}
            overlap = len(title_tokens & name_tokens)
            if overlap >= 2 and overlap > best_score:
                best = pts
                best_score = overlap
        if best is not None and best_score >= 2:
            _set_section_evaluation_weight(section, best)
    return sections


def max_rfp_outline_sections(page_limit: int | None = None) -> int:
    """Hard ceiling for RFP outline tabs (excludes static Sections 1–3).

    Page-budget heuristic: ~55% of narrative words for RFP tabs at ~400 words/tab.
    Without a page limit, default as if ~20 pages. Absolute floor 8 / ceiling 18 —
    evaluators cannot finish a 30–40 tab manuscript.
    """
    pages = page_limit if page_limit and page_limit > 0 else 20
    soft = int((pages * 350 * 0.55) // 400)
    return max(8, min(18, soft))


def enforce_outline_section_cap(
    sections: list[Any],
    max_n: int,
) -> tuple[list[Any], list[str]]:
    """Keep at most ``max_n`` outline tabs, preferring closing + scored + required.

    Deterministic post-planner safety net when the LLM emits one tab per bullet /
    criterion. Reorders by priority for selection, then restores original order.
    """
    if max_n <= 0 or len(sections) <= max_n:
        return list(sections), []

    def _title(section: Any) -> str:
        if hasattr(section, "title"):
            return str(section.title or "")
        if isinstance(section, dict):
            return str(section.get("title") or "")
        return ""

    def _required(section: Any) -> bool:
        if hasattr(section, "required"):
            return bool(section.required)
        if isinstance(section, dict):
            return bool(section.get("required"))
        return False

    def _weight(section: Any) -> float:
        pts = _section_evaluation_points(section)
        try:
            return float(pts) if pts is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    ranked = sorted(
        enumerate(sections),
        key=lambda pair: (
            0 if is_important_or_closing_outline_title(_title(pair[1])) else 1,
            0 if section_carries_evaluation_points(pair[1]) else 1,
            -_weight(pair[1]),
            0 if _required(pair[1]) else 1,
            pair[0],
        ),
    )
    keep_idx = {idx for idx, _ in ranked[:max_n]}
    kept = [sec for i, sec in enumerate(sections) if i in keep_idx]
    dropped = [
        f"{_title(sec)} (outline hard-cap {max_n})"
        for i, sec in enumerate(sections)
        if i not in keep_idx
    ]
    for i, section in enumerate(kept, start=1):
        if hasattr(section, "order"):
            section.order = i
        elif isinstance(section, dict):
            section["order"] = i
    return kept, dropped


def is_generic_filler_outline_title(title: str) -> bool:
    """Short/vague invented labels that should be enriched (or dropped if not in RFP)."""
    if is_important_or_closing_outline_title(title):
        return False
    core = normalize_outline_title(title)
    if not core:
        return True
    if _GENERIC_FILLER_TITLES.match(core):
        return True
    tokens = outline_title_tokens(title)
    return len(tokens) <= 1 and core in {
        "approach",
        "scope",
        "pricing",
        "price",
        "budget",
        "fees",
        "cost",
        "staffing",
        "team",
        "experience",
        "qualifications",
    }


def enrich_outline_title_from_rfp(title: str, rfp_context: str) -> str:
    """Prefer the RFP's fuller heading — never simplify to a boring short label."""
    core = normalize_outline_title(title)
    if not core or not (rfp_context or "").strip():
        return title
    # Already specific enough
    if len(outline_title_tokens(title)) >= 4 and not is_generic_filler_outline_title(title):
        return title

    best = ""
    core_tokens = outline_title_tokens(title)
    skip_starts = re.compile(
        r"^(?:submit|include|provide|attach|complete|the\s+|a\s+|an\s+|please\b)",
        re.IGNORECASE,
    )
    for raw_line in (rfp_context or "").splitlines():
        line = raw_line.strip().strip("•*-–—")
        if len(line) < 8 or len(line) > 140:
            continue
        if skip_starts.match(line):
            continue
        numbered = bool(re.match(r"^\d+(?:\.\d+)*\s+", line))
        title_case = bool(
            re.match(r"^[A-Z][A-Za-z0-9][A-Za-z0-9\s/,&\-]{3,}$", line)
        )
        all_caps = bool(re.match(r"^[A-Z0-9][A-Z0-9\s/,&\-]{4,}$", line))
        if not (numbered or title_case or all_caps):
            continue
        norm = normalize_outline_title(line)
        if not norm:
            continue
        # Prefer longer RFP heading that contains our short label
        if core and (core == norm or f" {core} " in f" {norm} " or core in norm):
            if len(norm) > len(normalize_outline_title(best or "")):
                best = line
            continue
        line_tokens = outline_title_tokens(line)
        if core_tokens and core_tokens <= line_tokens and len(line_tokens) > len(core_tokens):
            if len(norm) > len(normalize_outline_title(best or "")):
                best = line
        synonyms = {
            "price": {"cost", "pricing", "fee", "fees", "budget"},
            "pricing": {"cost", "price", "fee", "fees", "budget"},
            "budget": {"cost", "price", "pricing", "fee", "fees"},
            "fees": {"cost", "price", "pricing", "fee", "budget"},
            "cost": {"price", "pricing", "fee", "fees", "budget"},
            "qualifications": {"qualification", "experience", "capability", "capabilities"},
            "experience": {"qualifications", "qualification", "performance", "portfolio"},
            "references": {"reference", "customers", "clients"},
        }
        syn = synonyms.get(core, set())
        if syn and (line_tokens & syn) and len(line_tokens) >= 2:
            if len(norm) > len(normalize_outline_title(best or "")):
                best = line
    if not best:
        return title
    enriched = re.sub(r"\s+", " ", best).strip(" .-–—:")
    # Never replace a longer concrete title with a shorter boring one.
    if len(normalize_outline_title(enriched)) < len(core):
        return title
    return enriched or title


def filter_lean_outline_sections(
    sections: list[Any],
    *,
    rfp_context: str = "",
    drop_generic_filler: bool = True,
) -> tuple[list[Any], list[str]]:
    """Enrich titles, drop true static/near-dups, keep important + closing tabs.

    When ``drop_generic_filler`` is False (assembler re-pass without RFP text),
    only static + near-duplicate hygiene runs.
    """
    rfp_blob = (rfp_context or "").casefold()
    kept: list[Any] = []
    dropped: list[str] = []

    def _title(section: Any) -> str:
        if hasattr(section, "title"):
            return str(section.title or "")
        if isinstance(section, dict):
            return str(section.get("title") or "")
        return ""

    def _set_title(section: Any, title: str) -> None:
        if hasattr(section, "title"):
            section.title = title
        elif isinstance(section, dict):
            section["title"] = title

    def _required(section: Any) -> bool:
        if hasattr(section, "required"):
            return bool(section.required)
        if isinstance(section, dict):
            return bool(section.get("required", True))
        return True

    def _dup_static(section: Any) -> str | None:
        if hasattr(section, "duplicate_of_static_section"):
            return section.duplicate_of_static_section
        if isinstance(section, dict):
            return (
                section.get("duplicateOfStaticSection")
                or section.get("duplicate_of_static_section")
            )
        return None

    ordered = sorted(
        enumerate(sections),
        key=lambda pair: (0 if _required(pair[1]) else 1, pair[0]),
    )

    for _, section in ordered:
        original_title = _title(section)
        if is_kb_artefact_outline_title(original_title):
            dropped.append(f"{original_title} (knowledge-base filename, not a section)")
            continue
        title = enrich_outline_title_from_rfp(original_title, rfp_context)
        # A section carrying evaluation points is never dropped as generic
        # filler or a static duplicate — see section_carries_evaluation_points.
        scored = section_carries_evaluation_points(section)
        if not scored and (
            should_skip_rfp_section_as_static_duplicate(
                title=title,
                duplicate_of_static_section=_dup_static(section),
            )
            or is_duplicate_static_rfp_section(title)
        ):
            # Phase 2 owns this: tabs already covered by Sections 1–3 never
            # reach Phase 3 drafting. Sample-work / agency-requirements / scored
            # tabs are excluded inside is_duplicate_static_rfp_section itself —
            # do not re-protect them here via "important/closing" title lists.
            dropped.append(f"{original_title} (owned by Sections 1–3)")
            continue
        if not scored and drop_generic_filler and is_generic_filler_outline_title(title):
            # A topic being MENTIONED in the RFP is not a request for a section
            # about it. Procedural clauses (addenda process, PERA retiree
            # notification, sex-offender registration) are standing obligations,
            # not proposal contents — keeping a tab for each pushed the
            # manuscript past its page limit with content nobody asked for.
            from app.services.proposal_closing_package import rfp_requires_topic

            core = normalize_outline_title(title)
            terms = [core] + [
                tok for tok in outline_title_tokens(title) if len(tok) >= 4
            ]
            # With no RFP text we cannot know what was requested — keep the
            # section rather than silently emptying the outline.
            # TOC / proposal-contents headings count as demanded even without
            # nearby "shall submit" verbs (those verbs often sit on a parent
            # sentence several lines above the numbered list).
            requested = (not (rfp_context or "").strip()) or rfp_requires_topic(
                rfp_context, terms
            ) or rfp_lists_section_heading(rfp_context, title)
            if not requested and not is_important_or_closing_outline_title(title):
                mentioned = bool(core and core in rfp_blob)
                reason = (
                    "mentioned but not requested" if mentioned else "generic filler"
                )
                dropped.append(f"{original_title} ({reason})")
                continue
        if any(outline_titles_near_duplicate(title, _title(prev)) for prev in kept):
            # Prefer the longer / more specific title when near-dup.
            # Never drop a scored tab in favor of an unscored near-dup.
            prev_idx = next(
                i
                for i, prev in enumerate(kept)
                if outline_titles_near_duplicate(title, _title(prev))
            )
            prev = kept[prev_idx]
            prev_scored = section_carries_evaluation_points(prev)
            if scored and not prev_scored:
                dropped.append(f"{_title(prev)} (near-duplicate → kept scored tab)")
                kept[prev_idx] = section
                if title != original_title:
                    _set_title(section, title)
                continue
            if prev_scored and not scored:
                dropped.append(f"{original_title} (near-duplicate of scored tab)")
                continue
            prev_title = _title(prev)
            if len(normalize_outline_title(title)) > len(normalize_outline_title(prev_title)):
                _set_title(kept[prev_idx], title)
                dropped.append(f"{prev_title} (near-duplicate → kept fuller title)")
            else:
                dropped.append(f"{original_title} (near-duplicate)")
            continue
        if title != original_title:
            _set_title(section, title)
        kept.append(section)

    kept, agency_dropped = collapse_agency_requirements_siblings(kept)
    dropped.extend(agency_dropped)

    kept.sort(
        key=lambda s: next(
            (i for i, orig in enumerate(sections) if orig is s),
            10_000,
        )
    )
    for i, section in enumerate(kept, start=1):
        if hasattr(section, "order"):
            section.order = i
        elif isinstance(section, dict):
            section["order"] = i
    return kept, dropped


def merge_closing_components_into_outline(
    sections: list[Any],
    *,
    rfp_context: str,
) -> tuple[list[Any], list[str]]:
    """Ensure RFP-detected closing package tabs appear in the Intelligence outline."""
    from app.services.proposal_closing_package import (
        detect_closing_components,
        draft_already_covers_component,
    )
    from app.services.proposal_intelligence.schemas import OutlineSection

    # Obligation-gated only — an unrequested closing tab consumes page budget
    # that belongs to sections the RFP actually requires.
    components = detect_closing_components(rfp_context)
    if not components:
        return sections, []

    titles = []
    ids: set[str] = set()
    for section in sections:
        if hasattr(section, "title"):
            titles.append(str(section.title or ""))
            ids.add(str(getattr(section, "id", "") or ""))
        elif isinstance(section, dict):
            titles.append(str(section.get("title") or ""))
            ids.add(str(section.get("id") or ""))

    added_labels: list[str] = []
    out = list(sections)
    order_base = len(out)
    for i, component in enumerate(components, start=1):
        if draft_already_covers_component(
            draft_section_ids=ids,
            draft_titles=titles,
            component=component,
        ):
            continue
        title = enrich_outline_title_from_rfp(component.title, rfp_context)
        # Closing package must not re-add a near-dup of an outline tab that
        # already covers the same ask under a fuller RFP-phrased title.
        from app.services.proposal_outline_dedup import outline_titles_near_duplicate

        if any(outline_titles_near_duplicate(title, prev) for prev in titles):
            continue
        section = OutlineSection(
            id=component.section_id,
            title=title,
            order=order_base + i,
            required=True,
            conditionalReason=f"Closing package — {component.match_hint}",
        )
        out.append(section)
        ids.add(component.section_id)
        titles.append(title)
        added_labels.append(title)

    for i, section in enumerate(out, start=1):
        if hasattr(section, "order"):
            section.order = i
        elif isinstance(section, dict):
            section["order"] = i
    return out, added_labels
