"""Retrieve loss/debrief patterns from Supermemory (08_ lost, 09_ scoring) for this RFP."""

from __future__ import annotations

import logging
from typing import Any

from app.models.proposal import LossLesson
from app.models.rfp import RfpRecord
from app.services import llm, supermemory
from app.services.proposal_knowledge_base_tools import search_knowledge_base

logger = logging.getLogger(__name__)

# Keep search count low — Supermemory quota is shared with Phase 2 / pricing
MAX_LOSS_SEARCHES = 5

LOSS_SYNTHESIS_PROMPT = """You analyze zö agency LOST proposals (08_) and scoring/debrief documents (09_) to guide THIS active bid.

Extract patterns that caused losses or low scores — only from the KB excerpts provided, never generic advice.

Focus on what applies to THIS RFP's sector, contract type, evaluation model, and scope. Examples of valid patterns:
- Generic boilerplate when RFP required client-specific detail
- Wrong pricing model (fixed-fee vs T&E) or rates not aligned to evaluators
- Missing named key personnel / weak team section
- Case studies that didn't match sector or scope
- Ignoring page limits, attachment format, or scored section weights
- Voice/tone mismatches (too corporate, filler phrases evaluators penalized)

Return ONLY JSON:
{
  "lessons": [
    {
      "pattern": "what zö did wrong or what evaluators penalized",
      "avoid": "concrete writing/structural choice to avoid in this bid",
      "reason": "why it hurt the score or outcome",
      "source": "08_ or 09_ filename from excerpts",
      "relevance": "high | medium | low"
    }
  ],
  "writingAvoidances": [
    "Short imperative bullet for drafters, e.g. 'Do not use generic case study intros — tie each to OCTA rail audience'"
  ]
}

Max 8 lessons. If excerpts are empty or unrelated, return fewer lessons and note gaps in writingAvoidances.
Do NOT invent past losses not described in excerpts."""

DEFAULT_LOST_QUERIES = [
    "zö agency 08 LOST proposal debrief evaluator feedback weaknesses",
    "zö agency lost proposal scoring reasons public sector",
    "zö agency 09 scoring debrief evaluation rubric lost points",
]


async def _search_loss_kb(query: str, category: str) -> tuple[str, list[str], bool]:
    """Return text, sources, quota_exceeded."""
    try:
        text, sources = await search_knowledge_base(query, limit=6, category=category)
        quota = "search failed" in text.lower() and "supermemory" in text.lower()
        return text, sources, quota
    except Exception:
        return "(Search failed.)", [], False


async def gather_loss_kb_excerpts(
    *,
    rfp: RfpRecord,
    rfp_context: str,
) -> tuple[str, list[str], bool]:
    """Sequential KB pulls from lost_proposal + scoring_debrief (quota-safe)."""
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", [], False

    sector = rfp.sector
    client = rfp.client
    queries = [
        (DEFAULT_LOST_QUERIES[0], "lost_proposal"),
        (f"zö agency lost {sector} RFP proposal lessons learned", "lost_proposal"),
        (DEFAULT_LOST_QUERIES[2], "scoring_debrief"),
        (f"zö agency 09 debrief {sector} evaluation criteria", "scoring_debrief"),
        (f"zö agency FOIA competitor winner vs lost {sector}", "lost_proposal"),
    ][:MAX_LOSS_SEARCHES]

    chunks: list[str] = []
    sources: list[str] = []
    quota_hit = False

    for query, category in queries:
        text, srcs, _ = await _search_loss_kb(query, category)
        if text and not text.startswith("("):
            label = category.replace("_", " ")
            chunks.append(f"--- {label}: {query[:80]} ---\n{text}")
        for src in srcs:
            if src not in sources:
                sources.append(src)

    if not chunks:
        text, srcs, _ = await _search_loss_kb(
            "zö agency lost proposal debrief scoring",
            "lost_proposal",
        )
        if text and not text.startswith("("):
            chunks.append(text)
        sources.extend(s for s in srcs if s not in sources)

    combined = "\n\n".join(chunks)[:20_000]
    return (
        combined or "(No 08_ lost or 09_ scoring content in Supermemory — ingest loss/debrief files.)",
        sources,
        quota_hit,
    )


def _parse_lessons(raw: dict[str, Any]) -> tuple[list[LossLesson], list[str]]:
    lessons_raw = raw.get("lessons", [])
    lessons: list[LossLesson] = []
    if isinstance(lessons_raw, list):
        for item in lessons_raw:
            if not isinstance(item, dict):
                continue
            try:
                lessons.append(LossLesson.model_validate(item))
            except Exception:
                continue

    avoid = raw.get("writingAvoidances") or raw.get("writing_avoidances") or []
    avoidances = [str(a).strip() for a in avoid if isinstance(a, str) and str(a).strip()]

    if not avoidances and lessons:
        avoidances = [lesson.avoid for lesson in lessons[:6] if lesson.avoid]

    return lessons, avoidances


async def build_loss_lessons_for_rfp(
    *,
    rfp: RfpRecord,
    rfp_context: str,
) -> tuple[list[LossLesson], list[str], list[str]]:
    """Return loss lessons, writing avoidances, and KB sources used."""
    kb_text, sources, quota_hit = await gather_loss_kb_excerpts(
        rfp=rfp,
        rfp_context=rfp_context,
    )

    if "(No 08_" in kb_text or not sources:
        return [], [
            "No 08_LOST_ or 09_SCORE_ debrief excerpts in Supermemory for this bid — "
            "ingest loss/debrief files, then re-run proposal research.",
            "[Ingest 08_LOST_ and 09_SCORE_ files into Supermemory for bid-specific loss patterns]",
        ], sources

    if not llm.is_configured():
        flag = "[Loss lessons skipped — LLM not configured]"
        return [], [flag], sources

    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": LOSS_SYNTHESIS_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Active RFP: {rfp.title}\n"
                    f"Client: {rfp.client} / {rfp.sector} / {rfp.location or '(n/a)'}\n\n"
                    f"RFP excerpt:\n{rfp_context[:18_000]}\n\n"
                    f"Lost proposal & scoring debrief KB excerpts:\n{kb_text}"
                ),
            },
        ],
        max_tokens=3072,
        temperature=0.25,
    )

    lessons, avoidances = _parse_lessons(raw)

    if quota_hit:
        avoidances.append(
            "[Supermemory quota reached during loss-lesson search — retry later or upgrade plan]"
        )

    logger.info(
        "Loss lessons for %s: %d patterns, %d avoidances, %d KB sources",
        rfp.id,
        len(lessons),
        len(avoidances),
        len(sources),
    )
    return lessons, avoidances, sources


def format_avoidance_block(avoidances: list[str], lessons: list[LossLesson] | None = None) -> str:
    """Prompt block for Phase 3 drafting and section improve."""
    lines: list[str] = []
    if avoidances:
        lines.append("WRITING AVOIDANCES (from zö lost bids / debriefs — apply to this RFP):")
        lines.extend(f"- {a}" for a in avoidances[:10])
    if lessons:
        high = [lesson for lesson in lessons if lesson.relevance == "high"][:4]
        if high:
            lines.append("\nHIGH-RELEVANCE LOSS PATTERNS:")
            for lesson in high:
                lines.append(f"- AVOID: {lesson.avoid} (was: {lesson.pattern})")
    return "\n".join(lines) if lines else ""
