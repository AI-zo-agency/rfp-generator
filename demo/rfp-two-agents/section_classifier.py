"""Stage 1 — classify RFP document sections before semantic extraction (demo)."""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent1_tools import RfpDoc

# (section_type, search queries, extraction_priority 1=highest)
SECTION_CLASSIFIERS: list[tuple[str, list[str], int]] = [
    ("solicitation_metadata", ["key information", "solicitation number", "rfq", "procurement"], 1),
    ("key_dates", ["important dates", "schedule", "questions due", "quotes due", "due date"], 1),
    ("submission_instructions", ["instructions to offeror", "how to submit", "quote submission"], 1),
    ("submittal_items", ["submittal items", "required submittals", "proposal content"], 1),
    ("required_forms", ["required forms", "exhibit", "attachment", "form pc"], 1),
    ("qualifications", ["qualifications", "minimum qualifications", "experience requirement"], 2),
    ("references", ["references", "professional references", "project examples"], 2),
    ("staffing", ["staffing", "key personnel", "project team", "resumes"], 2),
    ("pricing", ["payment schedule", "pricing", "cost proposal", "compensation"], 2),
    ("evaluation", ["quote evaluation", "evaluation criteria", "selection criteria", "award basis"], 2),
    ("purpose_background", ["purpose", "background", "introduction"], 1),
    ("goals_objectives", ["goals", "objectives", "desired outcome"], 1),
    ("statement_of_work", ["statement of work", "scope of work", "scope of services", "deliverables"], 1),
    ("optional_as_needed_scope", ["as needed", "at county request", "optional services", "may request"], 3),
    ("task_order_process", ["task order", "work order", "authorization to proceed"], 2),
    ("draft_agreement", ["draft agreement", "sample agreement", "terms and conditions", "general provisions"], 4),
    ("insurance", ["insurance requirements", "certificate of insurance", "exhibit b"], 3),
    ("post_award", ["post-award", "upon award", "if selected", "successful offeror", "contractor shall"], 3),
    ("legal_boilerplate", ["governing law", "indemnification", "confidentiality", "dispute resolution"], 5),
]

_TEMPLATE_MARKERS = re.compile(
    r"\[#|\$#{2,}|_{4,}|include this paragraph if|otherwise delete|\[insert",
    re.I,
)

# When extracting target X, ignore sections with priority >= this threshold
_BOILERPLATE_PRIORITY = 4


_STOP_HEADINGS = (
    "draft agreement",
    "attachment ",
    "general provisions",
    "insurance requirements",
    "instructions to offeror",
    "submittal items",
    "quote evaluation",
    "payment schedule",
)


def _line_looks_like_heading(line: str) -> bool:
    s = line.strip()
    if len(s) < 6 or len(s) > 140:
        return False
    if re.match(r"^\d+(?:\.\d+){0,4}\s+[A-Z]", s):
        return True
    if s.isupper() and sum(c.isalpha() for c in s) >= 8:
        return True
    return False


def find_heading_start(doc: "RfpDoc", title_patterns: list[str]) -> tuple[int, str] | None:
    """First page + heading line matching any pattern (heading-like line preferred)."""
    for page_no, page in enumerate(doc.pages, start=1):
        for line in re.split(r"[\n\r]+", page or ""):
            stripped = line.strip()
            if len(stripped) < 5:
                continue
            low = stripped.casefold()
            for pat in title_patterns:
                if pat.casefold() not in low:
                    continue
                # Avoid matching body text that mentions draft agreement inside SOW chunk
                if pat.casefold() == "draft agreement" and not _line_looks_like_heading(stripped):
                    if "sample" not in low and "draft agreement" not in low[:25]:
                        continue
                if _line_looks_like_heading(stripped) or pat.casefold() in low[: max(20, len(pat) + 5)]:
                    return page_no, stripped
    return None


def _section_major_number(heading: str) -> int | None:
    m = re.match(r"^(\d+)(?:\.|\s)", heading.strip())
    return int(m.group(1)) if m else None


def _page_has_stop_heading(page: str, *, sow_major: int | None) -> bool:
    """Stop only on heading-like lines, not body mentions of 'draft agreement'."""
    for line in re.split(r"[\n\r]+", page or ""):
        stripped = line.strip()
        if not _line_looks_like_heading(stripped):
            continue
        low = stripped.casefold()
        if sow_major is not None:
            major = _section_major_number(stripped)
            if major is not None and major > sow_major:
                return True
            if major == sow_major:
                continue
        for stop in _STOP_HEADINGS:
            if stop in low and _line_looks_like_heading(stripped):
                if sow_major and "draft agreement" in low and major is None:
                    continue
                return True
    return False


def bounded_section_text(
    doc: "RfpDoc",
    *,
    start_page: int,
    max_pages: int = 28,
    char_cap: int = 28_000,
    start_heading: str = "",
    section_type: str = "",
) -> str:
    """Text from start_page until next major heading (not arbitrary page substring)."""
    sow_major = _section_major_number(start_heading) if section_type == "statement_of_work" else None
    parts: list[str] = []
    total = 0
    for p in range(start_page, min(doc.page_count, start_page + max_pages) + 1):
        page = doc.pages[p - 1] or ""
        if p > start_page and _page_has_stop_heading(page, sow_major=sow_major):
            break
        block = f"[p{p}]\n{page}"
        if total + len(block) > char_cap:
            block = block[: char_cap - total]
            parts.append(block)
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def build_bounded_sections(doc: "RfpDoc") -> dict[str, Any]:
    """Heading-bounded sections for extraction (Stage 1)."""
    mapping: list[tuple[str, list[str], int]] = [
        ("statement_of_work", ["statement of work", "scope of work", "scope of services"], 1),
        ("submittal_items", ["submittal items", "required submittals"], 1),
        ("required_forms", ["required forms", "offeror's cover page"], 1),
        ("evaluation", ["quote evaluation", "evaluation criteria", "selection criteria"], 2),
        ("pricing", ["payment schedule", "compensation schedule"], 2),
        ("references", ["references", "professional references"], 2),
        ("key_dates", ["important dates", "schedule of events", "key dates"], 1),
        ("purpose_background", ["purpose", "background"], 1),
        ("draft_agreement", ["draft agreement", "sample agreement"], 5),
    ]
    bounded: dict[str, Any] = {}
    for section_type, patterns, priority in mapping:
        start = find_heading_start(doc, patterns)
        if not start:
            bounded[section_type] = {"found": False, "text": None, "priority": priority}
            continue
        page_no, heading = start
        max_p = 40 if section_type == "statement_of_work" else 14
        cap = 36_000 if section_type == "statement_of_work" else 28_000
        text = bounded_section_text(
            doc,
            start_page=page_no,
            max_pages=max_p,
            char_cap=cap,
            start_heading=heading,
            section_type=section_type,
        )
        bounded[section_type] = {
            "found": True,
            "anchorPage": page_no,
            "heading": heading[:120],
            "priority": priority,
            "text": text[:28_000],
        }
    return bounded


def _best_anchor(doc: "RfpDoc", queries: list[str]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for q in queries:
        for hit in (doc.search_rfp(q, limit=3).get("results") or []):
            sc = float(hit.get("score") or 0)
            if sc > best_score:
                best_score = sc
                best = hit
    if not best or best_score < 2:
        return None
    return {**best, "score": best_score}


def classify_document_sections(doc: "RfpDoc") -> dict[str, Any]:
    """Return section types found + template-heavy pages (Stage 1)."""
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for section_type, queries, priority in SECTION_CLASSIFIERS:
        anchor = _best_anchor(doc, queries)
        if anchor:
            found.append(
                {
                    "type": section_type,
                    "priority": priority,
                    "anchorPage": int(anchor["page"]),
                    "score": anchor["score"],
                }
            )
        else:
            missing.append(section_type)

    template_pages: list[int] = []
    for page_no, page in enumerate(doc.pages, start=1):
        if _TEMPLATE_MARKERS.search(page or ""):
            template_pages.append(page_no)

    # Sort: lower priority number = more authoritative for understanding/compliance/scope
    found.sort(key=lambda x: (x["priority"], x["anchorPage"]))
    return {
        "sections": found,
        "missingTypes": missing,
        "templatePages": template_pages[:40],
        "boilerplatePriorityThreshold": _BOILERPLATE_PRIORITY,
    }


def section_text_for_type(
    doc: "RfpDoc",
    classification: dict[str, Any],
    section_type: str,
    *,
    char_cap: int = 6000,
    bounded: dict[str, Any] | None = None,
) -> str | None:
    """Pull bounded section text when available."""
    if bounded and section_type in bounded:
        sec = bounded[section_type]
        if isinstance(sec, dict) and sec.get("found") and sec.get("text"):
            return str(sec["text"])[:char_cap]
    for row in classification.get("sections") or []:
        if row.get("type") != section_type:
            continue
        page = int(row.get("anchorPage") or 1)
        block = doc.read_rfp_pages(max(1, page - 1), min(doc.page_count, page + 2))
        text = "\n\n".join(f"[p{p['page']}] {p['text']}" for p in block.get("pages") or [])
        return text[:char_cap]
    return None
