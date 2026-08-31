"""Build LLM-friendly RFP excerpts — keep submission/closing pages for long PDFs."""

from __future__ import annotations

import re

# Windows around these patterns are always included (71-page RFPs often omit mid-body in head+tail truncate).
_PRIORITY_PATTERNS: tuple[str, ...] = (
    r"table\s+of\s+contents",
    r"proposal\s+format|submission\s+requirements|instructions\s+to\s+(?:offerors|proposers|vendors)",
    r"evaluation\s+criteria|scoring|points?\s+will\s+be|overall\s+capabilities|brand\s+marketing\s+plan",
    r"cost\s+points?\s+conversion|price\s+reasonableness|familiarity\s+with",
    r"(?:fixed[\s-]?price|not\s+to\s+exceed|NTE|contract\s+(?:ceiling|value|amount)|"
    r"maximum\s+(?:contract|compensation|budget)|total\s+(?:contract|project)\s+value)",
    r"year\s*(?:1|2|3|one|two|three).{0,40}\$[\d,]+",
    r"\breferences?\b",
    r"three\s+customers?",
    r"two[- ]year|like\s+institution|community\s+college",
    r"public\s+entities?\s+and\s+colleges",
    r"vendor\s+(?:questionnaire|certification)",
    r"pricing\s+proposal\s+form|cost\s+proposal\s+form|quotation\s*/?\s*pricing",
    r"hourly.{0,120}monthly.{0,120}annual",
    r"alteration\s+or\s+departure|disqualif(?:y|ication)",
    r"non[- ]?collusion|statement\s+of\s+ownership",
    r"section\s+5\.9|5\.9\s+insurance|commercial general liability|"
    r"minimum\s+(?:insurance\s+)?(?:limits|coverage)",
    r"exemplar\s+agreement|sample\s+agreement",
    r"exhibit\s+a|brand marketing plan.{0,120}vision",
    r"key performance indicator|activity measure",
    r"cost factor|cost points|price reasonableness|lowest.{0,40}cost",
    r"attachment\s+0?1|excel.{0,40}worksheet",
    r"contractor.{0,80}responsible.{0,40}key performance",
    r"documents?\s+to\s+be\s+submitted",
    r"must be returned with (?:the )?proposal",
    r"vendor\s+qualification|financial\s+stability",
    r"awards?\s*(?:and|&)\s*recognition",
    r"offeror must (?:have|establish).{0,60}office|office in Oceania",
)

# CRITICAL windows are reserved budget BEFORE _PRIORITY_PATTERNS compete for it.
#
# Why this tier exists: priority windows are filled in DOCUMENT ORDER against a
# shared budget. On a 99k-char, 42-page RFP whose scored criteria form sits at
# ~65% (CNM P-472, Exhibit 1 on pages 27–31), early matches — table of
# contents, Sections A/B terms — consumed the whole window budget and the
# entire 1,000-point scoring table was cut. Downstream everything then behaved
# correctly on an excerpt that simply did not contain the criteria: extraction
# returned zero scored criteria, the outline kept only exhibit tabs, and the
# proposal answered none of the scored sections.
#
# A scoring table is the single most important passage in an RFP — it IS the
# required outline. It gets its own reserved budget and a wider span, because
# these tables run long (25 numbered rows here) and losing the tail loses
# sections just as completely as losing the head.
_CRITICAL_PATTERNS: tuple[str, ...] = (
    r"evaluation\s+criteria\s*(?:/\s*bid)?\s*(?:response|bid)?\s*form",
    r"up\s+to\s+[\d,]+\s+points?\s+possible",
    r"points?\s+possible",
    r"total\s+points?\s+(?:possible|available)",
    r"maximum\s+(?:of\s+)?[\d,]+\s+characters",
    r"weighted\s+evaluation\s+criteria",
)

# Tight span, because scoring-table matches are DENSE (one per scored row) and
# merge into a single contiguous window anyway. A wide span instead pads each
# incidental mention elsewhere in the document with thousands of wasted chars.
_CRITICAL_SPAN = 1_500

# Share of the window budget reserved for critical passages before priority
# patterns compete. The rest still goes to submission/forms/references windows.
_CRITICAL_BUDGET_SHARE = 0.55

_REFERENCE_SPEC_RE = re.compile(
    r"(?:references?\s*[—–-].{0,400}|"
    r"three\s+customers?.{0,400}|"
    r"like\s+institution.{0,400}|"
    r"public\s+entities?\s+and\s+colleges.{0,400})",
    re.I | re.S,
)

_QUOTATION_ALTERATION_RE = re.compile(
    r"alteration\s+or\s+departure.{0,200}quotation|"
    r"not\s+consider\s+any\s+quotation.{0,200}alteration|"
    r"disqualif(?:y|ication).{0,120}quotation|"
    r"contractors?\s+are\s+not\s+to\s+make\s+any\s+changes\s+to\s+the\s+quotation",
    re.I | re.S,
)


def rfp_forbids_quotation_form_changes(rfp_text: str) -> bool:
    """True when RFP says altering the official pricing/quotation form disqualifies the bid."""
    return bool(_QUOTATION_ALTERATION_RE.search(rfp_text or ""))


def extract_reference_requirement_summary(rfp_text: str, *, max_chars: int = 1200) -> str | None:
    """Pull verbatim-ish RFP language about references for closing-section prompts."""
    text = rfp_text or ""
    chunks: list[str] = []
    for m in _REFERENCE_SPEC_RE.finditer(text):
        snippet = re.sub(r"\s+", " ", m.group(0)).strip()
        if len(snippet) > 40 and snippet not in chunks:
            chunks.append(snippet)
    if not chunks:
        return None
    joined = " … ".join(chunks)
    return joined[:max_chars]


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not windows:
        return []
    windows.sort()
    merged: list[tuple[int, int]] = [windows[0]]
    for start, end in windows[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 500:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _windows_for(body: str, patterns: tuple[str, ...], span: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))
    return _merge_windows(windows)


def _match_density(body: str, window: tuple[int, int], patterns: tuple[str, ...]) -> int:
    """How many pattern hits fall inside a window.

    Document order is the wrong way to spend a scoring-table budget. A passing
    mention of "points possible" in an early instructions paragraph is one hit;
    the actual criteria table is thirty-two. Ranking by density spends the
    reserved budget on the real table wherever it sits in the document —
    Exhibit 1 at 56% through a 42-page RFP lost every time under document
    order, which is exactly how a 1,000-point scoreboard went missing.
    """
    chunk = body[window[0] : window[1]]
    return sum(len(re.findall(pat, chunk, flags=re.I | re.S)) for pat in patterns)


def _subtract_covered(
    window: tuple[int, int],
    covered: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Window minus any span already selected, so budget is never spent twice."""
    pieces = [window]
    for cs, ce in covered:
        nxt: list[tuple[int, int]] = []
        for ps, pe in pieces:
            if ce <= ps or cs >= pe:
                nxt.append((ps, pe))
                continue
            if ps < cs:
                nxt.append((ps, cs))
            if ce < pe:
                nxt.append((ce, pe))
        pieces = nxt
    return [(s, e) for s, e in pieces if e - s >= 400]


def _take_windows(
    candidates: list[tuple[int, int]],
    *,
    budget: int,
    already: list[tuple[int, int]],
) -> tuple[list[tuple[int, int]], int]:
    taken: list[tuple[int, int]] = []
    used = 0
    for window in candidates:
        if used >= budget:
            break
        for start, end in _subtract_covered(window, already + taken):
            if used >= budget:
                break
            room = budget - used
            if room < 400:
                break
            if end - start > room:
                end = start + room
            taken.append((start, end))
            used += end - start
    return taken, used


def build_priority_rfp_excerpt(text: str, *, max_chars: int = 50_000) -> str:
    """Head + priority windows + tail so mid-RFP forms (e.g. page ~28) are not dropped."""
    body = (text or "").strip()
    if not body:
        return ""
    if len(body) <= max_chars:
        return body

    head_budget = min(int(max_chars * 0.32), 22_000)
    tail_budget = min(int(max_chars * 0.22), 14_000)
    window_budget = max_chars - head_budget - tail_budget - 400

    # Head and tail are always kept; never let them be re-selected as windows.
    head_end = head_budget
    tail_start = len(body) - tail_budget
    already: list[tuple[int, int]] = [(0, head_end), (tail_start, len(body))]

    # Scoring tables first, against reserved budget — see _CRITICAL_PATTERNS.
    critical_budget = int(window_budget * _CRITICAL_BUDGET_SHARE)
    critical_windows = _windows_for(body, _CRITICAL_PATTERNS, _CRITICAL_SPAN)
    # Densest scoring passage first, not the earliest one.
    critical_windows.sort(
        key=lambda w: (-_match_density(body, w, _CRITICAL_PATTERNS), w[0])
    )
    critical_taken, critical_used = _take_windows(
        critical_windows,
        budget=critical_budget,
        already=already,
    )
    # Unused critical budget flows back to the priority tier — never wasted.
    priority_taken, _ = _take_windows(
        _windows_for(body, _PRIORITY_PATTERNS, 3200),
        budget=window_budget - critical_used,
        already=already + critical_taken,
    )

    critical_spans = {span for span in critical_taken}
    selected = _merge_windows(list(critical_taken) + list(priority_taken))
    priority_parts: list[str] = []
    for start, end in selected:
        label = (
            "priority: SCORED EVALUATION CRITERIA / points table"
            if any(cs <= start < ce or start <= cs < end for cs, ce in critical_spans)
            else "priority: submission / references / pricing / forms"
        )
        priority_parts.append(
            f"\n\n[--- RFP excerpt ({label}) ---]\n{body[start:end]}"
        )

    head = body[:head_budget]
    tail = body[-tail_budget:]
    omitted = len(body) - head_budget - tail_budget
    middle_note = (
        f"\n\n[... omitted non-priority middle of RFP (~{omitted:,} chars); "
        f"priority submission passages retained below ...]\n"
    )
    return f"{head}{middle_note}{''.join(priority_parts)}\n\n[--- RFP closing excerpt ---]\n{tail}"


def closing_package_excerpt(rfp_text: str, *, max_chars: int = 32_000) -> str:
    """Smaller excerpt focused on references, pricing form, certs — for closing-section LLM calls."""
    body = (rfp_text or "").strip()
    if not body:
        return ""
    if len(body) <= max_chars:
        return body

    windows: list[tuple[int, int]] = []
    span = 4500
    for pat in _PRIORITY_PATTERNS[3:]:  # skip TOC-only patterns
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))

    merged = _merge_windows(windows)
    if not merged:
        return body[-max_chars:]

    parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used]
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


# Keywords the regex pattern tuple above has no entry for (e.g. mandatory
# accessibility/VPAT requirements) — kept as a separate plain-string list so
# a domain-keyword addition never has to touch the existing regex patterns.
# Case-insensitive substring search, no regex.
_PLAIN_SUBMISSION_KEYWORDS: tuple[str, ...] = (
    "accessibility",
    "vpat",
    "voluntary product accessibility template",
    "disability standards",
)


def _find_plain_keyword_windows(
    body: str, keywords: tuple[str, ...], *, span: int
) -> list[tuple[int, int]]:
    """Case-insensitive substring window search via ``str.find`` — no regex."""
    folded = body.casefold()
    windows: list[tuple[int, int]] = []
    for keyword in keywords:
        needle = keyword.casefold()
        search_from = 0
        while True:
            idx = folded.find(needle, search_from)
            if idx == -1:
                break
            windows.append((max(0, idx - span), min(len(body), idx + len(needle) + span)))
            search_from = idx + len(needle)
    return windows


def submission_documents_excerpt(rfp_text: str, *, max_chars: int = 46_000) -> str:
    """Documents to be submitted, forms to return, vendor qualifications.

    Pattern list is kept in sync with `list_submission_checklist_from_rfp`'s
    catalog (proposal_rfp_submission_requirements.py) — a form or attachment
    the RFP names as "Exhibit H" or "W-9" with no nearby "documents to be
    submitted" phrase must still pull a text window here, or the LLM
    inventory pass that actually creates the manuscript checklist tab never
    sees it at all.
    """
    body = (rfp_text or "").strip()
    if not body:
        return ""
    patterns = (
        r"documents?\s+to\s+be\s+submitted",
        r"forms?\s+provided\s+by",
        r"must be returned with (?:the )?proposal",
        r"submission\s+requirements",
        r"proposal\s+format",
        r"company\s+history\s+and\s+vendor",
        r"vendor\s+qualification",
        r"financial\s+stability",
        r"awards?\s*(?:and|&)\s*recognition",
        r"acknowledgement\s+of\s+addenda",
        r"section\s+iv",
        # Named exhibits/forms/attachments are how most gov/buyer RFPs actually
        # label required submissions — often with no "documents to be
        # submitted" heading anywhere nearby (e.g. a trailing appendix list).
        r"\bexhibit\s+[A-Z0-9]+\b",
        r"\bappendix\s+[A-Z0-9]+\b",
        r"\battachment\s+[A-Z0-9]+\b",
        r"non[- ]?collusion",
        r"affirmative action",
        r"statement of ownership|ownership disclosure",
        r"vendor questionnaire|contractor questionnaire",
        r"certificate(?:s)?\s+of\s+insurance|\bCOI\b",
        r"\bW[- ]?9\b",
        r"pricing\s+proposal\s+form|cost\s+proposal\s+form|quotation\s*/?\s*pricing",
        r"authorized\s+(?:representative|signatory|signature)|signature\s+(?:block|page)",
        r"contractor vendor certification|\bCVC\b",
        r"required\s+attachments?|documents?\s+to\s+(?:be\s+)?(?:submitted|included|attached)|submission\s+checklist",
        r"assurance of compliance",
    )
    windows: list[tuple[int, int]] = []
    span = 5500
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))
    windows.extend(_find_plain_keyword_windows(body, _PLAIN_SUBMISSION_KEYWORDS, span=span))
    merged = _merge_windows(windows)
    if not merged:
        return build_priority_rfp_excerpt(body, max_chars=max_chars)
    parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used]
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


def insurance_requirements_excerpt(rfp_text: str, *, max_chars: int = 14_000) -> str:
    """Section 5.9-style minimum limits for closing / insurance sections."""
    body = (rfp_text or "").strip()
    if not body:
        return ""
    patterns = (
        r"section\s+5\.9|5\.9\s+insurance",
        r"commercial general liability|general liability insurance",
        r"automobile liability|auto liability",
        r"errors?\s*(?:and|&)\s*omissions|professional liability|E&O",
        r"minimum\s+(?:insurance\s+)?(?:limits|coverage)",
        r"certificate(?:s)?\s+of\s+insurance|additional\s+insured",
    )
    windows: list[tuple[int, int]] = []
    span = 4500
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))
    merged = _merge_windows(windows)
    if not merged:
        return body[:max_chars]
    parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used]
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


def evaluation_and_kpi_excerpt(rfp_text: str, *, max_chars: int = 36_000) -> str:
    """KPI scope, evaluation criteria, cost scoring, budget attachment instructions."""
    body = (rfp_text or "").strip()
    if not body:
        return ""
    patterns = (
        r"key performance indicator|activity measure|kpi target",
        r"section\s+two|scope of work|background and scope",
        r"contract monitoring|evaluation criteria|criteria\s*#",
        r"cost factor|cost points|price reasonableness|lowest.{0,60}price",
        r"attachment\s+0?1|proposal format|submission requirements|items?\s*7",
        r"contractor.{0,100}responsible.{0,60}key performance",
        r"agency.{0,80}strategic plan.{0,80}key performance",
    )
    windows: list[tuple[int, int]] = []
    span = 5000
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))
    merged = _merge_windows(windows)
    if not merged:
        return build_priority_rfp_excerpt(body, max_chars=max_chars)
    parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used]
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


def budget_and_cost_excerpt(rfp_text: str, *, max_chars: int = 28_000) -> str:
    """RFP language about budget ceiling, cost scoring, fee forms, and quote requirements."""
    body = (rfp_text or "").strip()
    if not body:
        return ""
    patterns = (
        r"(?:fixed[\s-]?price|not\s+to\s+exceed|NTE|contract\s+(?:ceiling|value|amount)|"
        r"maximum\s+(?:contract|compensation|budget)|total\s+(?:contract|project)\s+value)",
        r"budget|funding|appropriat|available\s+funds|estimated\s+(?:value|cost|budget)",
        r"cost\s+(?:of\s+)?(?:base\s+)?proposal|cost\s+factor|cost\s+points|price\s+reasonableness",
        r"pricing\s+proposal\s+form|cost\s+proposal\s+form|schedule\s+of\s+fees|quotation",
        r"company\s+quote|detailed\s+specifications\s+and\s+pricing|fee\s+schedule",
        r"hourly.{0,80}monthly.{0,80}annual|lump\s+sum|fixed[\s-]?fee",
        r"evaluation\s+criteria.{0,200}cost|cost.{0,80}evaluation",
        r"year\s*(?:1|2|3|one|two|three).{0,40}\$[\d,]+",
        r"\$[\d,]{3,}",
    )
    windows: list[tuple[int, int]] = []
    span = 4500
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))
    merged = _merge_windows(windows)
    if not merged:
        return build_priority_rfp_excerpt(body, max_chars=max_chars)
    parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used]
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)
