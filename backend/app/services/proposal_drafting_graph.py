"""Phase 3: evidence-grounded drafting for RFP-mapped proposal sections."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.proposal import EvidenceItem, ProposalBrandVoice, ProposalSection, RfpSectionMap, LossLesson
from app.services.proposal_brand_voice import (
    classify_section_register,
    format_brand_voice_block,
    format_register_block,
)
from app.services.proposal_loss_lessons import format_avoidance_block
from app.services.proposal_drafting_prompts import (
    MODULAR_APPROACH_BLOCK,
    format_proof_points_block,
    format_weight_priority_block,
    is_modular_approach_section,
)
from app.services.proposal_voice_enforcement import (
    enforce_narrative_voice,
    is_duplicate_static_rfp_section,
)
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)

BATCH_SIZE = 1
DEFAULT_WORD_TARGET = 800
_LLM_SEMAPHORE = asyncio.Semaphore(1)

DRAFT_BATCH_PROMPT = """You draft zö agency proposal section content for a government/commercial RFP response.

Rules (strict):
1. Use ONLY facts from the evidence corpus provided. Cite each fact inline as [E1], [E2], etc.
2. Never invent clients, metrics, certifications, team members, or contract values not in evidence.
3. For requirements not covered by evidence, write [VERIFY: describe what must be confirmed] ONLY after checking every evidence excerpt — prefer citing [E#] when any excerpt partially answers the requirement.
4. For template/layout pulls (zoMode pull/select), include [DESIGNER NOTE: ...] and reference evidence.
5. Match the BRAND VOICE and REGISTER blocks for each section.
6. NARRATIVE sections (register=narrative): first person we/our — NEVER "The Vendor", "The Offeror", or third-person agency distance. RFP form language does not apply to narrative prose.
7. PROCUREMENT sections (register=procurement): formal third-person Vendor/Offeror language is OK for attachments, forms, and compliance tables.
8. Write complete, submission-ready prose (not bullet outlines unless the RFP requires bullets).
9. Apply WRITING AVOIDANCES from lost bids/debriefs when provided — do not repeat patterns that caused past losses.
10. Lead narrative sections with PROOF POINTS — specific case studies tied to requirements ("why we win").
11. For approach/marketing plan sections, use the MODULAR APPROACH block (Discover → Strategize → Create → Activate).
12. Highest evaluationWeight sections need the most depth, proof, and word count — match wordTarget.
13. Do NOT state pricing tier, dollar totals, lump sums, or fee tables in narrative sections — those belong in the Fees/Budget section only. Cross-reference instead.
14. When RFP requires portfolio, writing samples, or reference contacts, use evidence excerpts with [E#] citations — do not leave passive VERIFY placeholders if evidence contains samples or contacts.

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
    writing_avoidances: list[str]
    loss_lessons: list[dict[str, Any]]
    proof_points: list[dict[str, Any]]
    drafted_sections: list[dict[str, Any]]
    provider: str
    error: str | None


def _word_target(section: dict[str, Any]) -> int:
    page_limit = section.get("pageLimit") or section.get("page_limit")
    if isinstance(page_limit, int) and page_limit > 0:
        return max(400, page_limit * 350)
    weight = section.get("evaluationWeight") or section.get("evaluation_weight")
    if isinstance(weight, (int, float)) and weight > 0:
        w = int(weight)
        if w >= 30:
            return max(1400, w * 55)
        if w >= 20:
            return max(1000, w * 48)
        if w >= 10:
            return max(700, w * 42)
        return max(500, w * 40)
    return DEFAULT_WORD_TARGET


def _section_weight(section: dict[str, Any]) -> int:
    weight = section.get("evaluationWeight") or section.get("evaluation_weight")
    if isinstance(weight, (int, float)) and weight > 0:
        return int(weight)
    return 0


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
        return tagged[:20]
    return corpus[:12]


def _format_evidence_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        eid = item.get("id", "?")
        source = item.get("source", "document")
        excerpt = str(item.get("excerpt", ""))[:1800]
        lines.append(f"[{eid}] {source}\n{excerpt}")
    return "\n\n".join(lines) if lines else "(No evidence items tagged for this section.)"


def _brand_voice_block(
    brand_voice: dict[str, Any] | None,
    *,
    register: str = "narrative",
    rfp_client: str = "",
) -> str:
    reg = "procurement" if register == "procurement" else "narrative"
    return format_brand_voice_block(
        brand_voice,
        rfp_client=rfp_client,
        register=reg,  # type: ignore[arg-type]
    )


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
    try:
        return await _draft_batch_once(batch, state)
    except LlmError as exc:
        if len(batch) <= 1:
            raise
        logger.warning(
            "Phase 3 batch of %d failed (%s) — retrying one section at a time",
            len(batch),
            exc,
        )
        merged: list[dict[str, Any]] = []
        provider = state.get("provider") or _provider_name()
        for section in batch:
            try:
                results, batch_provider = await _draft_batch_once([section], state)
                merged.extend(results)
                provider = batch_provider
            except LlmError as single_exc:
                sid = str(section.get("id") or "")
                merged.append(
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
                            f"[VERIFY: Section drafting failed — {single_exc}. "
                            f"Re-run Phase 3 or draft manually.]"
                        ),
                        "status": "outline",
                        "kbRefs": [],
                    }
                )
        return merged, provider


async def _draft_batch_once(
    batch: list[dict[str, Any]],
    state: DraftingGraphState,
) -> tuple[list[dict[str, Any]], str]:
    corpus = state.get("evidence_corpus") or []
    batch_payload: list[dict[str, Any]] = []

    for section in batch:
        sid = str(section.get("id") or "")
        evidence = _evidence_for_section(sid, corpus)
        zo_mode = str(section.get("zoMode") or section.get("zo_mode") or "write")
        title = str(section.get("title") or sid)
        register = classify_section_register(
            section_id=sid,
            title=title,
            zo_mode=zo_mode,
        )
        batch_payload.append(
            {
                "sectionId": sid,
                "title": title,
                "register": register,
                "requirements": section.get("requirements") or [],
                "zoMode": zo_mode,
                "wordTarget": _word_target(section),
                "uncoveredRequirements": section.get("uncoveredRequirements")
                or section.get("uncovered_requirements")
                or [],
                "evidence": _format_evidence_block(evidence),
            }
        )

    narrative_sections = [p for p in batch_payload if p.get("register") == "narrative"]
    procurement_sections = [
        p for p in batch_payload if p.get("register") == "procurement"
    ]

    user_content = (
        f"Client: {state['rfp_client']}\n"
        f"Sector: {state['rfp_sector']}\n"
        f"Location: {state.get('rfp_location') or ''}\n"
        f"RFP: {state['rfp_title']}\n\n"
    )
    if narrative_sections:
        user_content += (
            "NARRATIVE sections in this batch (first person we/our — never The Vendor):\n"
            f"{format_register_block('narrative')}\n\n"
            f"Brand voice for narrative sections:\n"
            f"{_brand_voice_block(state.get('brand_voice'), register='narrative', rfp_client=state['rfp_client'])}\n\n"
        )
    if procurement_sections:
        user_content += (
            "PROCUREMENT sections in this batch (formal third-person OK):\n"
            f"{format_register_block('procurement')}\n\n"
        )
    zo_ctx = (state.get("zo_sections_context") or "").strip()
    if zo_ctx:
        user_content += (
            "Existing zö template sections 1–3 (reference for pull/select sections; "
            "do not duplicate verbatim — adapt to RFP section requirements):\n"
            f"{zo_ctx[:6000]}\n\n"
        )
    avoid_block = format_avoidance_block(
        state.get("writing_avoidances") or [],
        [
            LossLesson.model_validate(item)
            for item in (state.get("loss_lessons") or [])
            if isinstance(item, dict)
        ],
    )
    if avoid_block:
        user_content += f"{avoid_block}\n\n"

    weight_block = format_weight_priority_block(state.get("rfp_sections") or [])
    if weight_block:
        user_content += f"{weight_block}\n\n"

    proof_points = state.get("proof_points") or []
    if proof_points and narrative_sections:
        for payload in batch_payload:
            if payload.get("register") != "narrative":
                continue
            block = format_proof_points_block(
                proof_points,
                section_id=str(payload.get("sectionId") or ""),
                section_title=str(payload.get("title") or ""),
            )
            if block:
                user_content += f"{block}\n\n"
                break

    if any(is_modular_approach_section(str(p.get("title") or "")) for p in batch_payload):
        user_content += f"{MODULAR_APPROACH_BLOCK}\n\n"

    user_content += f"Sections to draft:\n{json.dumps(batch_payload, indent=2)}"

    async with _LLM_SEMAPHORE:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": DRAFT_BATCH_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=6144,
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
        else:
            zo_mode = str(section.get("zoMode") or section.get("zo_mode") or "write")
            content = enforce_narrative_voice(
                content,
                section_id=sid,
                title=str(section.get("title") or sid),
                zo_mode=zo_mode,
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
    skipped = [
        s for s in sections if is_duplicate_static_rfp_section(str(s.get("title") or ""))
    ]
    sections = [
        s
        for s in sections
        if not is_duplicate_static_rfp_section(str(s.get("title") or ""))
    ]
    sections = sorted(sections, key=_section_weight, reverse=True)
    if skipped:
        logger.info(
            "Phase 3 skipping %d RFP sections (duplicate of static Sections 1–3): %s",
            len(skipped),
            [s.get("title") for s in skipped[:5]],
        )
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
    writing_avoidances: list[str] | None = None,
    loss_lessons: list[LossLesson] | None = None,
    proof_points: list | None = None,
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
        "writing_avoidances": writing_avoidances or [],
        "loss_lessons": [
            lesson.model_dump(by_alias=True) for lesson in (loss_lessons or [])
        ],
        "proof_points": [
            p.model_dump(by_alias=True) if hasattr(p, "model_dump") else p
            for p in (proof_points or [])
        ],
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
