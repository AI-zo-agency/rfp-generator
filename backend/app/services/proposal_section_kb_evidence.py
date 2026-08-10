"""General section-improve KB packing — same quality path as kb_qa_loop.

No vertical hardcodes (tourism, SF Travel, etc.). Builds a retrieval question from
the section title, RFP needs, user ask, and names already in the draft, then packs
snippets via retrieve_for_question.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Markdown H2 / bold labels often name the client or project in the draft.
_DRAFT_ENTITY_RE = re.compile(
    r"(?m)^(?:#{1,3}\s+|\*\*)([A-Z][^#\n*]{2,80}?)(?:\*\*)?\s*$"
)

# Prefer evidence-heavy sections for packed RAG (others still use planned queries).
_EVIDENCE_HEAVY_RE = re.compile(
    r"(?is)\b("
    r"case\s+stud|our\s+work|references?|experience|examples?\s+of|"
    r"social\s+media|campaign|portfolio|proof|results?|kpi|"
    r"destination|tourism|visitor|accounts?\s+managed"
    r")\b"
)

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
7) Do not replace KB-backed prose with new long [VERIFY] blocks; prefer citing packed evidence."""

# User wants content pulled from KB into the open tab (not a chat-only answer).
_KB_FETCH_FILL_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:fetch|pull|get|grab|load|populate|fill)\b.{0,80}"
    r"(?:knowledge\s+base|\bkb\b|case\s*stud|from\s+kb|that\b|this\b|the\s+section)"
    r"|"
    r"\b(?:empty|missing|blank|no\s+content|is\s+empty)\b.{0,50}"
    r"\b(?:fetch|fill|pull|get)\b"
    r"|"
    r"\b(?:fetch|fill|pull)\b.{0,50}\b(?:empty|missing|blank)\b"
    r"|"
    r"\bfill\b.{0,80}(?:from|using|with)\s+(?:the\s+)?(?:knowledge\s+base|\bkb\b)"
    r"|"
    r"\b(?:knowledge\s+base|\bkb\b)\b.{0,40}\b(?:only|facts|details|content|data)\b"
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


def section_wants_packed_kb_evidence(
    *,
    section_title: str = "",
    section_content: str = "",
    user_message: str = "",
) -> bool:
    blob = f"{section_title}\n{user_message}\n{(section_content or '')[:1500]}"
    if user_asks_kb_fetch_or_fill(user_message):
        return True
    return bool(_EVIDENCE_HEAVY_RE.search(blob))


def draft_entity_hints(section_content: str, *, limit: int = 6) -> list[str]:
    """Pull client/project names from draft headings — dynamic, not a fixed list."""
    text = section_content or ""
    out: list[str] = []
    seen: set[str] = set()
    for match in _DRAFT_ENTITY_RE.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1)).strip(" -:|")
        # Skip boilerplate headings
        if len(name) < 4 or name.casefold() in {
            "strategy",
            "results",
            "approach",
            "overview",
            "challenge",
            "solution",
            "kpis",
            "recommendation",
            "tourism portfolio approach",
        }:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def build_section_kb_question(
    *,
    section_title: str,
    user_message: str = "",
    requirements: list[str] | None = None,
    section_content: str = "",
) -> str:
    """One retrieval question for retrieve_for_question (kb_qa_loop-style)."""
    from app.services.kb_rag_retrieve import build_retrieval_question_from_entry

    entities = draft_entity_hints(section_content)
    assets = [r.strip() for r in (requirements or []) if str(r).strip()][:8]
    assets.extend(e for e in entities if e not in assets)
    base = build_retrieval_question_from_entry(
        section_title=section_title,
        required_assets=assets,
        planner_queries=[user_message.strip()] if user_message.strip() else [],
        why_needed="",
    )
    if user_message.strip():
        return base
    return base


async def fetch_packed_section_kb_evidence(
    *,
    section_title: str,
    user_message: str = "",
    requirements: list[str] | None = None,
    section_content: str = "",
    max_chars: int = 12_000,
) -> tuple[str, list[str]]:
    """Pack KB context for section improve. Returns (block, sources)."""
    if not section_wants_packed_kb_evidence(
        section_title=section_title,
        section_content=section_content,
        user_message=user_message,
    ):
        return "", []

    from app.services.kb_rag_retrieve import retrieve_for_question

    question = build_section_kb_question(
        section_title=section_title,
        user_message=user_message,
        requirements=requirements,
        section_content=section_content,
    )
    try:
        context, sources, _queries = await retrieve_for_question(
            question,
            limit=8,
            max_chars=max_chars,
            threshold=0.15,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("packed section KB retrieve failed: %s", exc)
        return "", []

    text = (context or "").strip()
    if not text or text.startswith("("):
        return "", list(sources or [])

    block = (
        "=== PACKED KB EVIDENCE (cite facts/KPIs from here; do not invent) ===\n"
        f"{text}\n"
        f"Sources: {', '.join((sources or [])[:10]) or '(see snippets)'}"
    )
    logger.info(
        "packed section KB evidence title=%r chars=%d sources=%s",
        (section_title or "")[:60],
        len(text),
        (sources or [])[:5],
    )
    return block, list(sources or [])


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
    return {"heavy": _EVIDENCE_HEAVY_RE.pattern}
