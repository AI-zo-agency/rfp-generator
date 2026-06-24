"""Phase 3: evidence-grounded drafting for RFP-mapped proposal sections."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.proposal import EvidenceItem, ProposalBrandVoice, ProposalSection, RfpSectionMap
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)

BATCH_SIZE = 3
DEFAULT_WORD_TARGET = 800
_LLM_SEMAPHORE = asyncio.Semaphore(1)

DRAFT_BATCH_PROMPT = """You draft zö agency proposal section content for a government/commercial RFP response.

Rules (strict):
1. Use ONLY facts from the evidence corpus provided. Cite each fact inline as [E1], [E2], etc.
2. Never invent clients, metrics, certifications, team members, or contract values not in evidence.
3. For requirements not covered by evidence, write [VERIFY: describe what must be confirmed].
4. For template/layout pulls (zoMode pull/select), include [DESIGNER NOTE: ...] and reference evidence.
5. Match the brand voice block. Mirror RFP terminology where appropriate.
6. Write complete, submission-ready prose (not bullet outlines unless the RFP requires bullets).

Return ONLY JSON:
{
  "sections": [
    {
      "sectionId": "rfp-sec-1",
      "content": "full section prose with [E#] citations",
      "kbRefs": ["E1", "E3"],
      "designerNote": "optional layout note or null"
    }
  ]
}"""


class DraftingGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    rfp_sections: list[dict[str, Any]]
    evidence_corpus: list[dict[str, Any]]
    brand_voice: dict[str, Any]
    zo_sections_context: str
    drafted_sections: list[dict[str, Any]]
    provider: str
    error: str | None


def _word_target(section: dict[str, Any]) -> int:
    page_limit = section.get("pageLimit") or section.get("page_limit")
    if isinstance(page_limit, int) and page_limit > 0:
        return max(400, page_limit * 350)
    weight = section.get("evaluationWeight") or section.get("evaluation_weight")
    if isinstance(weight, (int, float)) and weight > 0:
        return max(500, int(weight) * 40)
    return DEFAULT_WORD_TARGET


def _evidence_for_section(
    section_id: str,
    corpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tagged = [
        item
        for item in corpus
        if section_id in (item.get("sectionIds") or item.get("section_ids") or [])
    ]
    if tagged:
        return tagged[:12]
    return corpus[:6]


def _format_evidence_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        eid = item.get("id", "?")
        source = item.get("source", "document")
        excerpt = str(item.get("excerpt", ""))[:1800]
        lines.append(f"[{eid}] {source}\n{excerpt}")
    return "\n\n".join(lines) if lines else "(No evidence items tagged for this section.)"


def _brand_voice_block(brand_voice: dict[str, Any] | None) -> str:
    if not brand_voice:
        return "Tone: professional, client-focused. Formality: semi-formal."
    lines = [
        f"Tone: {brand_voice.get('tone', '')}",
        f"Formality: {brand_voice.get('formality', 'semi-formal')}",
        f"Client expectations: {brand_voice.get('clientExpectations') or brand_voice.get('client_expectations', '')}",
    ]
    guidelines = brand_voice.get("voiceGuidelines") or brand_voice.get("voice_guidelines") or []
    if guidelines:
        lines.append("Guidelines:")
        lines.extend(f"- {g}" for g in guidelines)
    terms = brand_voice.get("keyTerms") or brand_voice.get("key_terms") or []
    if terms:
        lines.append(f"Key terms: {', '.join(str(t) for t in terms)}")
    return "\n".join(lines)


def _chunk_sections(sections: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [sections[i : i + size] for i in range(0, len(sections), size)]


def _extract_kb_refs(content: str, declared: list[str] | None) -> list[str]:
    from_text = re.findall(r"\[E(\d+)\]", content)
    refs = {f"E{n}" for n in from_text}
    if declared:
        for item in declared:
            refs.add(str(item).strip())
    return sorted(refs, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)


async def _draft_batch(
    batch: list[dict[str, Any]],
    state: DraftingGraphState,
) -> tuple[list[dict[str, Any]], str]:
    corpus = state.get("evidence_corpus") or []
    batch_payload: list[dict[str, Any]] = []

    for section in batch:
        sid = str(section.get("id") or "")
        evidence = _evidence_for_section(sid, corpus)
        batch_payload.append(
            {
                "sectionId": sid,
                "title": section.get("title"),
                "requirements": section.get("requirements") or [],
                "zoMode": section.get("zoMode") or section.get("zo_mode") or "write",
                "wordTarget": _word_target(section),
                "uncoveredRequirements": section.get("uncoveredRequirements")
                or section.get("uncovered_requirements")
                or [],
                "evidence": _format_evidence_block(evidence),
            }
        )

    user_content = (
        f"Client: {state['rfp_client']}\n"
        f"Sector: {state['rfp_sector']}\n"
        f"Location: {state.get('rfp_location') or ''}\n"
        f"RFP: {state['rfp_title']}\n\n"
        f"Brand voice:\n{_brand_voice_block(state.get('brand_voice'))}\n\n"
    )
    zo_ctx = (state.get("zo_sections_context") or "").strip()
    if zo_ctx:
        user_content += (
            "Existing zö template sections 1–3 (reference for pull/select sections; "
            "do not duplicate verbatim — adapt to RFP section requirements):\n"
            f"{zo_ctx[:6000]}\n\n"
        )
    user_content += f"Sections to draft:\n{json.dumps(batch_payload, indent=2)}"

    async with _LLM_SEMAPHORE:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": DRAFT_BATCH_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            temperature=0.35,
        )

    results: list[dict[str, Any]] = []
    drafted = raw.get("sections", [])
    drafted_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(drafted, list):
        for item in drafted:
            if isinstance(item, dict):
                sid = str(item.get("sectionId") or item.get("id") or "").strip()
                if sid:
                    drafted_by_id[sid] = item

    for section in batch:
        sid = str(section.get("id") or "")
        item = drafted_by_id.get(sid, {})
        content = str(item.get("content", "")).strip()
        if not content:
            reqs = section.get("requirements") or []
            content = (
                f"[VERIFY: Draft content for {section.get('title')} — "
                f"insufficient evidence in corpus. Requirements: "
                f"{'; '.join(str(r) for r in reqs[:3])}]"
            )
        kb_refs = _extract_kb_refs(content, item.get("kbRefs") or item.get("kb_refs"))
        results.append(
            {
                "id": sid,
                "title": str(section.get("title") or sid),
                "pageLimit": section.get("pageLimit") or section.get("page_limit"),
                "wordTarget": _word_target(section),
                "required": True,
                "custom": False,
                "source": "rfp",
                "mode": section.get("zoMode") or section.get("zo_mode") or "write",
                "content": content,
                "designerNote": item.get("designerNote") or item.get("designer_note"),
                "status": "generated" if content else "outline",
                "kbRefs": kb_refs,
            }
        )

    return results, provider


async def _draft_all_sections(state: DraftingGraphState) -> dict[str, Any]:
    sections = state.get("rfp_sections") or []
    if not sections:
        return {"error": "No RFP sections to draft. Run Phase 2 first."}

    all_drafted: list[dict[str, Any]] = []
    provider = state.get("provider") or _provider_name()

    batches = _chunk_sections(sections, BATCH_SIZE)
    logger.info(
        "Phase 3 drafting for %s: %d sections in %d batches",
        state.get("rfp_id"),
        len(sections),
        len(batches),
    )

    for index, batch in enumerate(batches, start=1):
        try:
            batch_results, batch_provider = await _draft_batch(batch, state)
            all_drafted.extend(batch_results)
            provider = batch_provider
            logger.info(
                "Phase 3 batch %d/%d complete for %s (%d sections)",
                index,
                len(batches),
                state.get("rfp_id"),
                len(batch_results),
            )
        except LlmError as exc:
            logger.warning(
                "Phase 3 batch %d failed for %s: %s",
                index,
                state.get("rfp_id"),
                exc,
            )
            for section in batch:
                sid = str(section.get("id") or "")
                all_drafted.append(
                    {
                        "id": sid,
                        "title": str(section.get("title") or sid),
                        "pageLimit": section.get("pageLimit"),
                        "wordTarget": _word_target(section),
                        "required": True,
                        "custom": False,
                        "source": "rfp",
                        "mode": section.get("zoMode") or "write",
                        "content": (
                            f"[VERIFY: Section drafting failed — {exc}. "
                            f"Re-run Phase 3 or draft manually.]"
                        ),
                        "status": "outline",
                        "kbRefs": [],
                    }
                )

    return {"drafted_sections": all_drafted, "provider": provider}


def _build_graph() -> Any:
    graph = StateGraph(DraftingGraphState)
    graph.add_node("draft_sections", _draft_all_sections)
    graph.add_edge(START, "draft_sections")
    graph.add_edge("draft_sections", END)
    return graph.compile()


_DRAFTING_GRAPH = _build_graph()


def _zo_sections_context(sections: list[ProposalSection]) -> str:
    blocks: list[str] = []
    for section in sections[:3]:
        if not section.content.strip():
            continue
        blocks.append(f"### {section.title}\n{section.content[:2500]}")
    return "\n\n".join(blocks)


async def run_drafting_graph(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    rfp_sections: list[RfpSectionMap],
    evidence_corpus: list[EvidenceItem],
    brand_voice: ProposalBrandVoice | None,
    zo_template_sections: list[ProposalSection] | None = None,
) -> tuple[list[ProposalSection], str]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    initial: DraftingGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "rfp_sections": [s.model_dump(by_alias=True) for s in rfp_sections],
        "evidence_corpus": [e.model_dump(by_alias=True) for e in evidence_corpus],
        "brand_voice": brand_voice.model_dump(by_alias=True) if brand_voice else {},
        "zo_sections_context": _zo_sections_context(zo_template_sections or []),
        "drafted_sections": [],
    }

    logger.info("Phase 3 drafting graph starting for rfp_id=%s", rfp_id)
    final = await _DRAFTING_GRAPH.ainvoke(initial)

    if final.get("error"):
        raise LlmError(str(final["error"]), status_code=400)

    sections = [
        ProposalSection.model_validate(item)
        for item in (final.get("drafted_sections") or [])
    ]
    provider = str(final.get("provider") or _provider_name())

    logger.info(
        "Phase 3 drafting complete for %s: %d sections drafted",
        rfp_id,
        len(sections),
    )
    return sections, provider
