"""LangGraph pipeline for static proposal Sections 1–3 (KB pull/select + dual-layer voice)."""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.proposal import ProposalBrandVoice, ProposalSection
from app.services import llm, proposal_knowledge_base_tools
from app.services.llm import LlmError
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)


class SectionsGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    page_limit: int | None
    brand_voice: dict[str, Any]
    kb_zo_voice: str
    kb_zo_voice_sources: list[str]
    kb_company: str
    kb_company_sources: list[str]
    kb_bios: str
    kb_bio_sources: list[str]
    kb_case_studies: str
    kb_case_sources: list[str]
    sections: list[dict[str, Any]]
    provider: str
    error: str | None


def _proposal_voice_block(state: SectionsGraphState) -> str:
    brand_voice = state.get("brand_voice") or {}
    zo_kb = (state.get("kb_zo_voice") or "").strip()
    guidelines = brand_voice.get("voiceGuidelines") or brand_voice.get("voice_guidelines") or []
    terms = brand_voice.get("keyTerms") or brand_voice.get("key_terms") or []

    lines = [
        "## zö core brand voice (from knowledge base — do not contradict)",
        brand_voice.get("zoCoreVoice") or brand_voice.get("zo_core_voice") or "(see KB excerpt)",
    ]
    if zo_kb:
        lines.append(zo_kb[:2500])

    lines.extend(
        [
            "",
            "## This RFP voice adaptation (must differ per client/sector)",
            f"Tone: {brand_voice.get('tone', '')}",
            f"Formality: {brand_voice.get('formality', 'semi-formal')}",
            f"Client expectations: {brand_voice.get('clientExpectations') or brand_voice.get('client_expectations', '')}",
        ]
    )
    adaptation = brand_voice.get("rfpAdaptationNotes") or brand_voice.get("rfp_adaptation_notes")
    if adaptation:
        lines.append(f"Adaptation notes: {adaptation}")
    if guidelines:
        lines.append("Writing guidelines for this proposal:")
        lines.extend(f"- {g}" for g in guidelines)
    if terms:
        lines.append(f"Mirror RFP terminology: {', '.join(str(t) for t in terms)}")

    return "\n".join(lines)


async def _fetch_knowledge_base(state: SectionsGraphState) -> dict[str, Any]:
    bundles = await proposal_knowledge_base_tools.gather_proposal_kb_for_sections(
        rfp_title=state["rfp_title"],
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_location=state.get("rfp_location"),
        rfp_context=state["rfp_context"],
    )
    zo_voice_text, zo_voice_sources = bundles["zo_voice"]
    company_text, company_sources = bundles["company"]
    bios_text, bio_sources = bundles["bios"]
    cases_text, case_sources = bundles["case_studies"]

    return {
        "kb_zo_voice": zo_voice_text,
        "kb_zo_voice_sources": zo_voice_sources,
        "kb_company": company_text,
        "kb_company_sources": company_sources,
        "kb_bios": bios_text,
        "kb_bio_sources": bio_sources,
        "kb_case_studies": cases_text,
        "kb_case_sources": case_sources,
    }


async def _synthesize_proposal_voice(state: SectionsGraphState) -> dict[str, Any]:
    """Merge zö KB brand voice with RFP-specific tone adaptation."""
    zo_kb = (state.get("kb_zo_voice") or "")[:8000]
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You define how zö agency should write THIS proposal.\n\n"
                        "Two layers (both required):\n"
                        "1. zö core voice — from the knowledge-base brand voice excerpt (identity, "
                        "personality, how zö always sounds).\n"
                        "2. RFP adaptation — how tone/formality/terminology should shift for THIS "
                        "client, sector, and solicitation (read the RFP closely).\n\n"
                        "Different RFPs MUST produce different adaptations (e.g., state agency formal "
                        "vs. tourism warm vs. higher-ed collaborative). Never generic one-size-fits-all.\n"
                        "Never contradict verified zö KB facts or invent zö positioning not in KB.\n\n"
                        "Return JSON only:\n"
                        '{"zoCoreVoice":"1-2 sentences from KB",'
                        '"tone":"combined label for this proposal",'
                        '"formality":"formal|semi-formal|conversational",'
                        '"voiceGuidelines":["specific writing rules for this RFP"],'
                        '"keyTerms":["terms to mirror from RFP"],'
                        '"clientExpectations":"what evaluators want to hear",'
                        '"rfpAdaptationNotes":"how this RFP voice differs from a generic zö proposal"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Client: {state['rfp_client']}\n"
                        f"Sector: {state['rfp_sector']}\n"
                        f"Title: {state['rfp_title']}\n\n"
                        f"zö brand voice (knowledge base):\n{zo_kb}\n\n"
                        f"RFP text:\n{state['rfp_context'][:12000]}"
                    ),
                },
            ],
            temperature=0.4,
        )
        logger.info(
            "Proposal voice synthesized for %s: tone=%r formality=%r",
            state.get("rfp_id"),
            raw.get("tone"),
            raw.get("formality"),
        )
        return {"brand_voice": raw, "provider": _provider_name()}
    except LlmError as exc:
        logger.warning("Proposal voice synthesis failed: %s", exc)
        return {
            "brand_voice": {
                "zoCoreVoice": "Professional, confident, human-centered marketing partner.",
                "tone": "professional",
                "formality": "semi-formal",
                "voiceGuidelines": [
                    "Lead with verified zö capabilities.",
                    f"Match {state['rfp_client']} public-sector formality.",
                ],
                "keyTerms": [],
                "clientExpectations": "",
                "rfpAdaptationNotes": "Fallback voice — re-run when LLM available.",
            },
            "provider": _provider_name(),
        }


def _section_system_preamble(state: SectionsGraphState) -> str:
    return (
        "You write zö agency proposal content.\n"
        "Facts (clients, certs, team, case studies) must come ONLY from knowledge-base excerpts.\n"
        "Voice must follow BOTH zö core brand voice AND the RFP-specific adaptation block — "
        "each proposal should read differently based on the client/RFP.\n"
        f"Client: {state['rfp_client']} | Sector: {state['rfp_sector']}\n"
    )


async def _build_section_1(state: SectionsGraphState) -> dict[str, Any]:
    voice = _proposal_voice_block(state)
    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    f"{_section_system_preamble(state)}"
                    "Write Section 1 — Company Overview.\n"
                    "PULL from KB facts only. Framing prose must match the voice block.\n"
                    'Return JSON: {"content":"...","designerNote":"...","kbRefs":["..."]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Word target: ~900\n\n"
                    f"Voice:\n{voice}\n\n"
                    f"Knowledge base (company overview / 02_):\n"
                    f"{state.get('kb_company', '')[:14000]}"
                ),
            },
        ],
        max_tokens=4096,
        temperature=0.4,
    )
    section = _section_payload(
        section_id="section-1-company-overview",
        title="Section 1 — Company Overview",
        mode="pull",
        word_target=900,
        page_limit=state.get("page_limit"),
        page_ratio=0.12,
        designer_note_default="PULL FROM MASTER TEMPLATE — Section 1. Designer uses master layout.",
        raw=raw,
        kb_sources=state.get("kb_company_sources") or [],
    )
    return {"sections": [section]}


async def _build_section_2(state: SectionsGraphState) -> dict[str, Any]:
    voice = _proposal_voice_block(state)
    existing = state.get("sections") or []
    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    f"{_section_system_preamble(state)}"
                    "Write Section 2 — Team Overview.\n"
                    "SELECT bios from KB only — do not embellish credentials.\n"
                    "Framing paragraph must use this RFP's adapted voice.\n"
                    'Return JSON: {"content":"...","designerNote":"...","kbRefs":["..."],'
                    '"layout":"full-page|multi|overview","bios":["Name 1"]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Word target: ~1200\n\n"
                    f"Voice:\n{voice}\n\n"
                    f"RFP context:\n{state['rfp_context'][:4000]}\n\n"
                    f"Team bios KB:\n{state.get('kb_bios', '')[:14000]}"
                ),
            },
        ],
        max_tokens=4096,
        temperature=0.4,
    )
    section = _section_payload(
        section_id="section-2-team-overview",
        title="Section 2 — Team Overview",
        mode="select",
        word_target=1200,
        page_limit=state.get("page_limit"),
        page_ratio=0.15,
        designer_note_default="Select bio layout from master template. Insert exact bio text — no rewrites.",
        raw=raw,
        kb_sources=state.get("kb_bio_sources") or [],
        extra_refs=raw.get("bios") if isinstance(raw.get("bios"), list) else [],
    )
    return {"sections": [*existing, section]}


async def _build_section_3(state: SectionsGraphState) -> dict[str, Any]:
    voice = _proposal_voice_block(state)
    existing = state.get("sections") or []
    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    f"{_section_system_preamble(state)}"
                    "Write Section 3 — Our Work (Case Studies).\n"
                    "SELECT 2–4 verified case studies from KB. Intro must match this RFP's voice.\n"
                    'Return JSON: {"content":"...","designerNote":"...","kbRefs":["..."],'
                    '"selected":["case study 1"]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Word target: ~1500\n\n"
                    f"Voice:\n{voice}\n\n"
                    f"RFP context:\n{state['rfp_context'][:5000]}\n\n"
                    f"Case studies KB:\n{state.get('kb_case_studies', '')[:16000]}"
                ),
            },
        ],
        max_tokens=4096,
        temperature=0.4,
    )
    section = _section_payload(
        section_id="section-3-our-work",
        title="Section 3 — Our Work (Case Studies)",
        mode="select",
        word_target=1500,
        page_limit=state.get("page_limit"),
        page_ratio=0.18,
        designer_note_default="Select 2–4 verified 03_CS_ case studies by sector/scope match.",
        raw=raw,
        kb_sources=state.get("kb_case_sources") or [],
        extra_refs=raw.get("selected") if isinstance(raw.get("selected"), list) else [],
    )
    return {"sections": [*existing, section]}


def _section_payload(
    *,
    section_id: str,
    title: str,
    mode: str,
    word_target: int,
    page_limit: int | None,
    page_ratio: float,
    designer_note_default: str,
    raw: dict[str, Any],
    kb_sources: list[str],
    extra_refs: list[str] | None = None,
) -> dict[str, Any]:
    content = str(raw.get("content", "")).strip()
    designer = str(raw.get("designerNote") or designer_note_default).strip()
    kb_refs = raw.get("kbRefs") if isinstance(raw.get("kbRefs"), list) else []
    refs = list(
        dict.fromkeys([*(str(r) for r in kb_refs), *(extra_refs or []), *kb_sources[:5]])
    )
    budget = page_limit or 30
    return {
        "id": section_id,
        "title": title,
        "pageLimit": max(1, int(budget * page_ratio)),
        "wordTarget": word_target,
        "required": True,
        "custom": False,
        "source": "template",
        "mode": mode,
        "content": content,
        "designerNote": designer,
        "status": "generated" if content else "outline",
        "kbRefs": refs,
    }


def _build_graph() -> Any:
    graph = StateGraph(SectionsGraphState)
    graph.add_node("fetch_knowledge_base", _fetch_knowledge_base)
    graph.add_node("synthesize_proposal_voice", _synthesize_proposal_voice)
    graph.add_node("build_section_1", _build_section_1)
    graph.add_node("build_section_2", _build_section_2)
    graph.add_node("build_section_3", _build_section_3)

    graph.add_edge(START, "fetch_knowledge_base")
    graph.add_edge("fetch_knowledge_base", "synthesize_proposal_voice")
    graph.add_edge("synthesize_proposal_voice", "build_section_1")
    graph.add_edge("build_section_1", "build_section_2")
    graph.add_edge("build_section_2", "build_section_3")
    graph.add_edge("build_section_3", END)
    return graph.compile()


_SECTIONS_GRAPH = _build_graph()


async def run_sections_1_3_graph(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    page_limit: int | None,
) -> tuple[list[ProposalSection], ProposalBrandVoice, str]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    initial: SectionsGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "page_limit": page_limit,
        "sections": [],
    }

    logger.info("LangGraph sections 1–3 starting for rfp_id=%s", rfp_id)
    final = await _SECTIONS_GRAPH.ainvoke(initial)

    if final.get("error"):
        raise LlmError(str(final["error"]), status_code=502)

    raw_sections = final.get("sections") or []
    sections = [ProposalSection.model_validate(item) for item in raw_sections]
    brand_voice = ProposalBrandVoice.model_validate(final.get("brand_voice") or {})
    provider = str(final.get("provider") or _provider_name())

    logger.info(
        "LangGraph sections 1–3 complete for %s: %d sections, tone=%r, provider=%s",
        rfp_id,
        len(sections),
        brand_voice.tone,
        provider,
    )
    return sections, brand_voice, provider
