"""Cross-section anti-duplication — each section has one job; no manuscript rehash."""

from __future__ import annotations

import re
from typing import Any

from app.services.proposal_section_quality import word_count

ANTI_DUPLICATION_RULES = """## ANTI-DUPLICATION (mandatory — manuscript must feel tight)

Each section is INDEPENDENT and has ONE job. Do NOT re-explain material that belongs elsewhere.

OWNED BY STATIC SECTIONS (mention once with a short pointer, never re-write):
- Company identity / Who We Are / Our Promise → Section 1.1
- Org roster, FEIN, address, certifications, insurance → Section 1.2–1.5
- Full team bios and titles → Section 2
- Full case studies with Challenge / What We Did / Outcome → Section 3

OWNED BY RFP TABS (write only the part THIS tab scores):
- Understanding / Opportunity → client goals, constraints, audiences — NOT company bio
- Methodology / Approach → process steps for THIS scope — NOT case studies or Who We Are
- Timeline / Schedule → phases and dates — NOT methodology paragraphs again
- Budget / Fees → compensation model and transparency — NOT approach restatement
- References → contacts only — NOT experience narratives

RULES:
1. If a fact already appears in a prior section digests block below, do NOT paste it again.
2. One brief cross-reference is OK ("As detailed in Section 2…") then ADD new detail only.
3. Prefer concise paragraphs over repeating brand story, MWBE status, or office locations.
4. Case study names: at most one short proof sentence outside Section 3 — no full rewrite.
5. Cut filler openers ("We are excited…", "As a full-service agency…") when Section 1 already covers identity.
6. Stay within wordTarget — denser beats longer when facts would otherwise repeat.
"""


def digest_section_for_dedup(
    title: str,
    content: str,
    *,
    max_chars: int = 420,
) -> str:
    """Compact digest of what a section already covers (for other section prompts)."""
    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text:
        return ""
    headings = re.findall(r"^#{1,3}\s+(.+)$", content or "", re.M)
    head_bit = ""
    if headings:
        head_bit = " | headings: " + "; ".join(h.strip()[:60] for h in headings[:6])
    excerpt = text[:max_chars]
    if len(text) > max_chars:
        excerpt = excerpt.rsplit(" ", 1)[0] + "…"
    words = word_count(content or "")
    return f"- **{title}** ({words}w){head_bit}: {excerpt}"


def format_prior_sections_block(
    prior_sections: list[dict[str, Any]] | list[Any],
    *,
    exclude_ids: set[str] | None = None,
    max_sections: int = 12,
    max_chars_each: int = 420,
) -> str:
    """Build 'already covered' digests so the LLM does not rehash other tabs."""
    exclude = exclude_ids or set()
    digests: list[str] = []
    for section in prior_sections:
        if hasattr(section, "model_dump"):
            data = section.model_dump(by_alias=True)
        elif isinstance(section, dict):
            data = section
        else:
            continue
        sid = str(data.get("id") or data.get("sectionId") or "")
        if sid in exclude:
            continue
        title = str(data.get("title") or sid)
        content = str(data.get("content") or "").strip()
        if not content:
            continue
        digests.append(
            digest_section_for_dedup(title, content, max_chars=max_chars_each)
        )
        if len(digests) >= max_sections:
            break
    if not digests:
        return ""
    return (
        "## ALREADY COVERED IN OTHER SECTIONS (do not repeat — add NEW detail only)\n"
        + "\n".join(digests)
    )


def format_anti_duplication_rules() -> str:
    return ANTI_DUPLICATION_RULES
