"""Build LLM-friendly RFP excerpts — keep submission/closing pages for long PDFs."""

from __future__ import annotations

import re

# Windows around these patterns are always included (71-page RFPs often omit mid-body in head+tail truncate).
_PRIORITY_PATTERNS: tuple[str, ...] = (
    r"table\s+of\s+contents",
    r"proposal\s+format|submission\s+requirements|instructions\s+to\s+(?:offerors|proposers|vendors)",
    r"evaluation\s+criteria|scoring|points?\s+will\s+be",
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
)

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

    windows: list[tuple[int, int]] = []
    span = 3200
    for pat in _PRIORITY_PATTERNS:
        for m in re.finditer(pat, body, flags=re.I | re.S):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))

    merged = _merge_windows(windows)
    priority_parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > window_budget:
            remaining = window_budget - used
            if remaining < 800:
                break
            chunk = chunk[:remaining]
        priority_parts.append(
            f"\n\n[--- RFP excerpt (priority: submission / references / pricing / forms) ---]\n{chunk}"
        )
        used += len(chunk)
        if used >= window_budget:
            break

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


def submission_documents_excerpt(rfp_text: str, *, max_chars: int = 38_000) -> str:
    """Documents to be submitted, forms to return, vendor qualifications."""
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
    )
    windows: list[tuple[int, int]] = []
    span = 5500
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
