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
2. One brief cross-reference is OK ("As detailed in Section 3.1…") then ADD new detail only.
3. Prefer concise paragraphs over repeating brand story, MWBE status, or office locations.
4. Case study names: at most one short proof sentence outside Section 3 — NEVER paste Challenge /
   Solution / Results blocks, client quotes, or metric lists that already appear in Section 3.
5. Past performance / references tabs: use a summary TABLE plus 2–3 sentences per project — NOT
   full case-study rewrites. Point readers to Section 3 for narrative detail.
6. Each KB case study may appear IN FULL exactly once (its Section 3 tab only).
7. Cut filler openers ("We are excited…", "As a full-service agency…") when Section 1 already covers identity.
8. Stay within wordTarget — denser beats longer when facts would otherwise repeat.
9. Do NOT create near-duplicate RFP tabs that restate the same proof already covered by
   Sections 1–3 or another scored tab. One section, one job, then stop.
10. Evaluators skim — hit the scored asks, then stop. Prefer tables and short bullets over essay
    padding. Never invent length with filler when the RFP ask is already covered.
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


def _client_key_from_title(title: str) -> str:
    raw = (title or "").strip()
    if "—" in raw:
        raw = raw.split("—", 1)[1].strip()
    elif " - " in raw:
        raw = raw.split(" - ", 1)[1].strip()
    raw = re.sub(r"^[\d.]+\s*", "", raw).strip()
    tokens = [t for t in re.split(r"\W+", raw.casefold()) if len(t) >= 4]
    generic = {
        "city",
        "county",
        "state",
        "digital",
        "campaign",
        "department",
        "employment",
        "brewery",
        "case",
        "study",
    }
    for t in tokens:
        if t not in generic:
            return t
    return tokens[0] if tokens else ""


def _distinctive_paragraphs(content: str, *, min_len: int = 60) -> list[str]:
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", content or ""):
        text = re.sub(r"\s+", " ", block.strip())
        if len(text) >= min_len and not text.startswith("[VERIFY"):
            paras.append(text)
    if not paras:
        for line in (content or "").splitlines():
            text = re.sub(r"\s+", " ", line.strip())
            if len(text) >= min_len and not text.startswith("#"):
                paras.append(text)
    return paras


def _plain_for_match(text: str) -> str:
    return re.sub(r"\*+", "", text).strip()


def compress_duplicate_case_study_sections(
    sections: list[Any],
) -> tuple[list[Any], int]:
    """Replace full case-study rewrites outside Section 3 with short pointers."""
    from app.models.proposal import ProposalSection

    s3_cards: list[tuple[str, str, str, list[str]]] = []
    for section in sections:
        if not isinstance(section, ProposalSection):
            continue
        if not section.id.startswith("section-3-work-") or section.id.endswith("placeholder"):
            continue
        key = _client_key_from_title(section.title or "")
        if not key:
            continue
        paras = _distinctive_paragraphs(section.content or "")
        s3_cards.append((key, section.id, section.title or "", paras))

    if not s3_cards:
        return sections, 0

    compressed = 0
    out: list[Any] = []
    for section in sections:
        if not isinstance(section, ProposalSection):
            out.append(section)
            continue
        if section.id.startswith("section-3-work-"):
            out.append(section)
            continue
        body = section.content or ""
        if not body.strip():
            out.append(section)
            continue

        new_body = body
        for key, sid, stitle, paras in s3_cards:
            plain_paras = [_plain_for_match(p) for p in paras]
            hits = sum(
                1
                for p in plain_paras
                if p and (p[:120] in new_body or p in new_body)
            )
            label = stitle.split("—", 1)[-1].strip() if "—" in stitle else stitle
            pointer = (
                f"See **{stitle}** in Our Work for the full case narrative "
                f"(Challenge, approach, and results)."
            )
            pattern = re.compile(
                rf"(^|\n)(#{{1,3}}\s+[^\n]*(?:{re.escape(label[:24])}|{re.escape(key)})[^\n]*\n)"
                rf"([\s\S]*?)(?=\n#{{1,3}}\s|\Z)",
                re.I,
            )
            if pattern.search(new_body):
                new_body = pattern.sub(rf"\1\2{pointer}\n\n", new_body, count=1)
                compressed += 1
                continue
            if hits < 1 and not any(p[:60] in new_body for p in plain_paras if p):
                continue
            for p in plain_paras:
                if p and p in new_body:
                    new_body = new_body.replace(p, pointer, 1)
                    compressed += 1
                    break

        if new_body != body:
            out.append(section.model_copy(update={"content": new_body.strip()}))
        else:
            out.append(section)

    return out, compressed
