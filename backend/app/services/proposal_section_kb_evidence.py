"""General section-improve KB packing — same quality path as kb_qa_loop.

Builds retrieve questions from the live user ask and mapped RFP requirements,
then packs snippets via retrieve_for_question. No static topic/query packs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ACCURATE_KB_EDITOR_RULES = """ACCURATE KB EVIDENCE RULES (mandatory):
1) Prefer facts from the PACKED KB EVIDENCE block below over vague prior draft prose.
2) When the block lists strategy, deliverables, or results/KPIs for a named client or
   project, write those into the section — do not replace them with a Sonja [VERIFY]
   asking for details that are already present.
3) Use [VERIFY: …] ONLY for discrete RFP fields that are truly absent from the packed
   evidence (and other evidence). Never invent numbers, %, overnight visitation,
   platforms, certifications, or contacts.
4) If evidence describes a different kind of engagement than the RFP asks for, say so
   honestly (cite what the KB actually shows) instead of fabricating a better match.
5) Do not claim team certifications or contacts unless they appear in evidence/bios.
6) Never write [VERIFY] tags that are instructions to Sonja/Ella or meta-commentary about
   RFP fit (e.g. "Request from Sonja whether…"). VERIFY labels must be short missing-field
   names only — or omit VERIFY and state the gap in plain prose.
7) Do not replace KB-backed prose with new long [VERIFY] blocks; prefer citing packed evidence.
8) FILL FIRST, DEFER ONLY WHAT YOU CANNOT KNOW. A handoff tag is for a fact that
   exists outside your reach — a phone number, a signature, a file. It is NOT for
   work you can do yourself. Judgement calls are yours: which past project best
   matches this RFP, why it is relevant, how to frame it. You have the case
   studies and you have the RFP, so decide and write it. Never emit a tag asking a
   human to "confirm relevance", "confirm framing", "review fit" or similar — that
   is your job, and handing it back reads as the agent refusing to think.
9) A DEFERRED FIELD MUST NAME ITSELF. When you genuinely must defer, the tag names
   the exact missing field and the entity it belongs to — "[MANUAL FILL: Sonja —
   phone number for Chantal Strobel, Deschutes Public Library]" — never a vague
   instruction like "provide reference contacts before submission". Commit to
   every part you DO have (the organization, the person, the scope, the email) and
   tag only the specific hole. A row naming a real client with one tagged field
   beats a paragraph explaining that you cannot supply references.
10) REFERENCES TABS: Prefer clients/contacts from PACKED KB that match the RFP's
    mapped reference requirements (count, sector, institution type). Do not keep a
    wrong prior client just because it was already in the draft. Incomplete contact
    fields → [VERIFY]/…]/ / [MANUAL FILL: Sonja — …] for that field only — never invent
    phone or email."""

# User wants content pulled from KB into the open tab (not a chat-only answer).
_KB_FETCH_FILL_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:fetch|pull|get|grab|load|populate|fill|correct)\b.{0,80}"
    r"(?:knowledge\s+base|\bkb\b|case\s*stud|from\s+kb|that\b|this\b|the\s+section|resume|04_bio|bio\b)"
    r"|"
    r"\b(?:empty|missing|blank|no\s+content|is\s+empty)\b.{0,50}"
    r"\b(?:fetch|fill|pull|get)\b"
    r"|"
    r"\b(?:fetch|fill|pull)\b.{0,50}\b(?:empty|missing|blank)\b"
    r"|"
    r"\bfill\b.{0,80}(?:from|using|with)\s+(?:the\s+)?(?:knowledge\s+base|\bkb\b|resume|04_bio)"
    r"|"
    r"\b(?:knowledge\s+base|\bkb\b)\b.{0,40}\b(?:only|facts|details|content|data)\b"
    r"|"
    r"\b(?:fetch|pull|get|grab|load|populate|fill|correct|update)\b.{0,60}"
    r"\b(?:from\s+)?(?:its|his|her|their)\s+resume\b"
    r"|"
    r"\bresume\b.{0,40}\b(?:info|facts|details|content|correct)\b"
    r")"
)


def user_asks_kb_fetch_or_fill(user_message: str) -> bool:
    """True when the user wants the draft filled from KB — must edit, not advise-only."""
    text = (user_message or "").strip()
    if not text:
        return False
    # Section swap/replace from KB is not "fetch into open tab" — structure planner handles.
    if re.search(
        r"(?is)\b(?:replace|swap|instead\s+of)\b.{0,120}\b(?:with|for)\b",
        text,
    ):
        return False
    if _KB_FETCH_FILL_RE.search(text):
        return True
    # General: retrieval verb + explicit KB reference (any section/topic, not only case studies).
    has_kb = bool(re.search(r"(?is)\b(?:knowledge\s+base|\bkb\b)\b", text))
    has_retrieval = bool(
        re.search(
            r"(?is)\b(?:fetch|pull|get|grab|load|populate|fill|look\s*up|search|find)\b",
            text,
        )
    )
    if has_kb and has_retrieval:
        return True
    if re.search(r"(?is)\bfrom\s+(?:the\s+)?(?:knowledge\s+base|\bkb\b)\b", text):
        return True
    return False


# NOTE: section_wants_packed_kb_evidence() was removed. It gated evidence packing on a
# regex of industry words (case stud|results|kpi|destination|tourism|visitor|…), so
# whether a section got grounded depended on the client's vertical vocabulary — in a
# module documented as having no vertical hardcodes. Callers that genuinely want to skip
# retrieval gate before calling; the rest always ground.


def section_prose_excerpt(section_content: str, *, limit: int = 400) -> str:
    """Whitespace-normalised head of the section, for semantic retrieval.

    Replaces the old draft_entity_hints(), which pulled "entities" with a regex that
    only matched markdown headings and then dropped any name appearing in a
    hardcoded stoplist. Both halves were wrong: a client named in an ordinary
    sentence was invisible, and the stoplist carried one client's phrase
    ("tourism portfolio approach") into every other client's retrieval.

    Handing the retriever the actual prose needs no extraction — Supermemory
    embeds the question, so real sentences carry the names, numbers, and context a
    pattern could only approximate.
    """
    return " ".join((section_content or "").split())[:limit]


def build_section_kb_question(
    *,
    section_title: str,
    user_message: str = "",
    requirements: list[str] | None = None,
    section_content: str = "",
    rfp_client: str = "",
    rfp_sector: str = "",
) -> str:
    """Retrieval question from live user ask + mapped RFP requirements (no static packs)."""
    ask = (user_message or "").strip()
    assets = [r.strip() for r in (requirements or []) if str(r).strip()][:8]
    # Lead with the ask (kb_qa_loop parity). Append live RFP requirement text so
    # References/improve searches the buyer's actual demand — not draft client names.
    if ask and assets:
        return f"{ask[:240]} RFP needs: {'; '.join(assets)[:280]}"
    if ask:
        return ask[:400]

    from app.services.kb_rag_retrieve import build_retrieval_question_from_entry

    return build_retrieval_question_from_entry(
        section_title=section_title,
        required_assets=assets,
        planner_queries=[],
        why_needed=section_prose_excerpt(section_content),
        rfp_client=rfp_client or rfp_sector,
    )


def _pack_questions(
    *,
    section_title: str,
    user_message: str,
    requirements: list[str] | None,
    section_content: str,
    rfp_client: str,
    rfp_sector: str,
) -> list[str]:
    """Build retrieve questions from live RFP requirements + user ask only."""
    questions: list[str] = []
    ask = (user_message or "").strip()
    if ask:
        questions.append(ask[:400])
    for req in (requirements or [])[:5]:
        text = str(req or "").strip()
        if text:
            questions.append(text[:400])
    # Sector/client from this RFP (live fields) — not a hardcoded contact template.
    context_bits = [
        (section_title or "").strip(),
        (rfp_sector or "").strip(),
        (rfp_client or "").strip(),
    ]
    context = " ".join(b for b in context_bits if b)
    if context and len(questions) < 2:
        questions.append(context[:400])
    if not questions:
        questions.append(
            build_section_kb_question(
                section_title=section_title,
                user_message=user_message,
                requirements=requirements,
                section_content=section_content,
                rfp_client=rfp_client,
                rfp_sector=rfp_sector,
            )
        )
    seen: set[str] = set()
    out: list[str] = []
    for q in questions:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out[:5]


async def fetch_packed_section_kb_evidence(
    *,
    section_title: str,
    user_message: str = "",
    requirements: list[str] | None = None,
    section_content: str = "",
    rfp_client: str = "",
    rfp_sector: str = "",
    max_chars: int = 12_000,
) -> tuple[str, list[str]]:
    """Pack KB context for section improve. Returns (block, sources).

    Uses the user ask and mapped RFP requirement text as Supermemory questions —
    no static query packs or filename pins.
    """
    from app.services.kb_rag_retrieve import retrieve_for_question

    questions = _pack_questions(
        section_title=section_title,
        user_message=user_message,
        requirements=requirements,
        section_content=section_content,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
    )

    parts: list[str] = []
    sources: list[str] = []
    seen_src: set[str] = set()
    budget = max(max_chars, 24_000)
    per_q = max(8_000, budget // max(len(questions), 1))

    for question in questions:
        q = (question or "").strip()
        if not q:
            continue
        remaining = budget - sum(len(p) for p in parts)
        if remaining < 800:
            break
        try:
            context, hit_sources, _queries = await retrieve_for_question(
                q,
                limit=12,
                max_chars=min(per_q, remaining),
                threshold=0.35,
                fallback_threshold=0.22,
                expand_queries=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("packed section KB retrieve failed for %r: %s", q[:60], exc)
            continue
        text = (context or "").strip()
        if not text or text.startswith("("):
            continue
        parts.append(text)
        for src in hit_sources or []:
            if src and src not in seen_src:
                seen_src.add(src)
                sources.append(src)

    if not parts:
        return "", sources

    merged = "\n\n".join(parts)
    if len(merged) > budget:
        merged = merged[:budget]
    block = (
        "=== PACKED KB EVIDENCE (cite facts/KPIs from here; do not invent) ===\n"
        f"{merged}\n"
        f"Sources: {', '.join(sources[:10]) or '(see snippets)'}"
    )
    logger.info(
        "packed section KB evidence title=%r chars=%d sources=%s",
        (section_title or "")[:60],
        len(merged),
        sources[:5],
    )
    return block, sources


def inject_packed_evidence_into_instruction(
    editor_instruction: str,
    packed_block: str,
) -> str:
    if not (packed_block or "").strip():
        return editor_instruction
    return (
        f"{(editor_instruction or '').strip()}\n\n"
        f"{ACCURATE_KB_EDITOR_RULES}\n\n"
        f"{packed_block.strip()}"
    )


# Test helper
def _extract_state_for_tests() -> dict[str, Any]:
    return {}
