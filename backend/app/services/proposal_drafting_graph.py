"""Phase 3: evidence-grounded drafting for RFP-mapped proposal sections."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
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
from app.services.proposal_intelligence.log import log_intel_event
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_draft_llm import (
    SECTION_DRAFT_FAILURE_PLACEHOLDER,
    chat_json_with_repair,
)
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)

BATCH_SIZE = 1
DEFAULT_WORD_TARGET = 800
# Cap concurrent LLM calls within a single RFP's drafting run — created per
# invocation in run_drafting_graph so unrelated RFPs never wait on each other.
LLM_CONCURRENCY = 1

SectionDraftedCallback = Callable[[list["ProposalSection"], str], Awaitable[None]]
_SECTION_DRAFT_CALLBACKS: dict[str, SectionDraftedCallback] = {}

DRAFT_BATCH_PROMPT = """You draft zö agency proposal section content for a government/commercial RFP response.

## CRITICAL: ANTI-HALLUCINATION RULES (ENFORCE STRICTLY) — DO NOT RELAX THESE

YOU MUST NEVER:
1. Invent statistics (retention rates, client counts, audience sizes, years of experience)
2. Cite specific numbers unless they appear VERBATIM in the evidence corpus with [E#] citation OR are stated in the RFP requirements / Proposal Execution Plan budget intel (e.g. RFP not-to-exceed / annual media spend)
3. Use team member names not in approved bio files (04_Bio_*.pdf in evidence)
4. Add certifications not explicitly in 01_companyfacts_verified evidence
5. Transfer metrics from one client project to describe agency-wide capabilities
6. Round or approximate numbers - use exact figures from evidence or [VERIFY: field]
7. Spell names incorrectly (check exact spelling in bio file evidence)
8. Claim "X years of Y experience" unless that exact phrasing is in verified evidence
9. Invent agency hourly rates, fee tables, or markups not grounded in 00_Guide_Pricing evidence / pricing plan

VERIFIED FACTS ONLY (from evidence corpus):
- Agency: founded August 21, 2013; years in operation = current year − 2013 (13 in 2026). Never say a different year count than Business Information.
- Certifications: WBENC, WOSB ONLY (no platform certs unless in verified evidence)
- Client retention: NEVER cite specific retention rate (not formally tracked per verified facts)
- Awards: Creative Excellence 2024, Netty 2024, NYX 2024, Vega Digital 2024, Sonja's Enterprising Women 2026
- Team: ONLY names from 04_Bio_*.pdf files in evidence
- Insurance/Certifications: Keep SHORT and CONCISE, list coverage types only, use [VERIFY: amounts] for dollar figures

IF YOU CANNOT VERIFY A COMPANY FACT IN EVIDENCE:
- Use [VERIFY: specific field needed] instead of inventing
- Never use "approximately," "around," "over X years" without evidence citation
- Do not embellish or extrapolate from partial information

ALLOWED WITHOUT inventing company facts (plan-driven structure):
- Restate RFP requirements, goals, constraints, and stated spend ceilings from the section requirements / Opportunity Understanding / Proposal Memory
- Describe methodology phases, timeline logic, governance cadence, and persuasion structure from Delivery Plan + Winning Pattern
- For Budget narrative: use transparency/pass-through language + 00_Guide_Pricing excerpts when present; defer invented role-hour fee tables to Phase 3.5
- NEVER return empty content for Understanding / Methodology / Timeline / Budget — write the section using plan + RFP requirements, and [VERIFY] only discrete missing facts

Rules (strict):
1. Never invent unverified company facts (metrics, clients, certifications, team members, contract awards). Those require evidence [E#] or [VERIFY].
2. Use ONLY facts from the evidence corpus. Do NOT insert markers like [E1] or [E2] in the written proposal — keep the prose client-ready.
3. For requirements not covered by evidence, write [VERIFY: describe what must be confirmed] ONLY for the missing fact — prefer citing [E#] when any excerpt partially answers. Do not blank the whole section.
4. For template/layout pulls (zoMode pull/select), include [DESIGNER NOTE: ...] and reference evidence.
5. Match the BRAND VOICE and REGISTER blocks for each section.
6. NARRATIVE sections (register=narrative): first person we/our — NEVER "The Vendor", "The Offeror", or third-person agency distance. RFP form language does not apply to narrative prose.
7. PROCUREMENT sections (register=procurement): formal third-person Vendor/Offeror language is OK for attachments, forms, and compliance tables.
8. Write complete, submission-ready prose (not bullet outlines unless the RFP requires bullets).
9. Apply WRITING AVOIDANCES from lost bids/debriefs when provided — do not repeat patterns that caused past losses.
10. Lead narrative sections with PROOF POINTS — specific case studies tied to requirements ("why we win").
11. For approach/marketing plan sections, use the MODULAR APPROACH block (Discover → Strategize → Create → Activate).
12. Highest evaluationWeight sections need the most depth, proof, and word count — match wordTarget.
13. For non-Budget narrative sections: do NOT invent pricing tiers, agency fee tables, or lump-sum totals — those belong in Fees/Budget / Phase 3.5. You may still mention the RFP's stated media budget if it appears in requirements/plan (e.g. $200,000 annual).
14. When RFP requires portfolio, writing samples, or reference contacts, use evidence excerpts with [E#] citations — do not leave passive VERIFY placeholders if evidence contains samples or contacts.
15. NEVER defer required submission data to unnamed attachments or "upon request" — include reference phones, workforce %, hours tables, or PSA acknowledgments in the proposal body.
16. References: when RFP requires contact names and phone numbers, include them inline (from KB) or [VERIFY: specific contact field].
17. Personnel: when RFP requires workforce diversity data, state headcount and minority/female percentages (from KB) or [VERIFY].
18. Budget section: when RFP requires staff hours per task, add hours table OR commission-model explanation with transparency estimates.
19. PSA/contract items in the RFP (insurance, living wage, MacBride, Title VI, audit rights, etc.) need brief acknowledgment sentences in the proposal.
20. References: NEVER "contact on request", "upon request", or "through the Bureau" — include name, title, organization, phone, and email from KB or [VERIFY: specific contact fields].
21. Workforce: MWBE/diversity and Project Personnel sections must use identical headcount and % female/minority — one precise figure from HR/KB.
22. Budget SUMMARY line-item fee tables (agency fees by role/hour) are built in Phase 3.5. Do NOT invent those tables or $0 placeholders. NEVER refuse to write the Budget section itself.
23. Insurance RFPs: include a limits table (RFP requires | current policy | gap | bind-before-execution action) with ACORD fields when specified.
24. Vendor/contractor questionnaires: complete every field — FEIN, phones, email, DUNS/CAGE or N/A — from KB; never leave TBD or blank underscores.
25. NJ or geography-specific reference RFPs: use verified KB contacts; if no in-state client exists, disclose geography honestly — never [PLACEHOLDER] reference rows.
26. Project management fees must stay within 5–8% of agency fees — do not leave unresolved PM ratio flags in budget prose.
27. Address every Phase 2 uncovered requirement explicitly — compliance tables, forms, or narrative; do not assume a titled section alone satisfies the RFP.
28. If a Winning Pattern is provided, use it only for structure, flow, tone, visuals, and persuasion strategy. Never copy, paraphrase, or cite prior won proposal prose.
29. Plan-driven narrative sections (Understanding / Methodology / Timeline / Budget overview) MUST be drafted even when evidence is thin or empty. Use RFP requirements, Opportunity Understanding, Section Strategy, Winning Pattern, and Proposal Memory. Cite [E#] only when evidence exists; do not refuse to write the whole section. Use [VERIFY: specific field] only for discrete missing facts, never as the entire section body.
30. Understanding sections should restate the client's goals, constraints, audiences, success measures, and risks in zö voice before pitching solution — show we read the RFP carefully.
31. When the section title is Budget / Pricing / Fees / Cost: you MUST write full narrative covering (a) transparent compensation philosophy, (b) pass-through / no hidden media markup commitment, (c) how media spend is allocated across RFP priorities with rationale, (d) that detailed agency fee tables follow in the pricing build. Ground compensation language in 00_Guide_Pricing evidence when present. Use RFP-stated spend amounts from requirements/plan. Leave only discrete unknown agency rate cells as [VERIFY: …], never blank the whole section. If the RFP forbids altering the official Quotation/Pricing Proposal Form, do NOT restructure the form into Section A/B/C/D — mirror the buyer's field labels only and put all rationale in a separate "Supporting Budget Rationale" section.
32. References sections: restate the RFP's required reference count and institution type when the RFP specifies them. Never claim the RFP is silent on references if requirements list three customers, two-year public, or NJ public-college reference tables. If zö lacks a qualifying reference, state the gap honestly and use [MANUAL FILL: leadership decision] — do not deny the requirement exists.
33. KPI scope: When the RFP distinguishes agency-wide/strategic-plan KPIs from CONTRACTOR-scored KPIs, commit ONLY to the contractor set (with numeric targets from Section 2 / monitoring). Never substitute the buyer's four agency KPIs for the three contractor KPIs.
34. Cost scoring: If the RFP uses inverse cost scoring (lowest responsive price gets maximum cost points), never claim that bidding at the published ceiling earns the highest cost rating — state the tradeoff honestly.
35. Cost weight: Use the RFP's stated criteria points for cost/price (sum Criteria #4 + #5 when both exist) — do not round to a generic "10%".
36. Budget container: When the RFP requires Attachment 01 / Excel budget worksheet, the narrative budget section must point to that file — not replace it with a PDF cost-category table.
37. ANTI-DUPLICATION: Each section has ONE job. Do not re-write Who We Are, full bios, full case studies, FEIN/address/certs, or brand story that belongs in Sections 1–3 or another RFP tab. One brief cross-reference is OK — then add NEW RFP-specific detail only. Prefer concise prose within wordTarget.

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
    execution_plan: dict[str, Any] | None
    brand_voice: dict[str, Any]
    zo_sections_context: str
    writing_avoidances: list[str]
    loss_lessons: list[dict[str, Any]]
    proof_points: list[dict[str, Any]]
    manuscript_locks: dict[str, Any] | None
    drafted_sections: list[dict[str, Any]]
    provider: str
    error: str | None
    llm_semaphore: asyncio.Semaphore


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


def order_sections_for_phase3_draft(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve Phase 2 outline/manuscript order for drafting sequence.

    Evaluation weight still drives word targets and prompt depth, but must not
    reorder drafting — weight-first left form/compliance tabs (weight 0) empty
    while later scored tabs filled, desyncing the UI from generation progress.
    """
    return list(sections)


def _phase3_content_is_usable(content: str | None) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if text == SECTION_DRAFT_FAILURE_PLACEHOLDER.strip():
        return False
    return True


def partition_phase3_sections(
    rfp_sections: list[RfpSectionMap],
    existing_by_id: dict[str, ProposalSection],
) -> tuple[list[RfpSectionMap], list[ProposalSection]]:
    """Split mapped sections into ones still needing a draft vs already filled."""
    to_draft: list[RfpSectionMap] = []
    already: list[ProposalSection] = []
    for mapped in rfp_sections:
        if is_duplicate_static_rfp_section(mapped.title):
            continue
        existing = existing_by_id.get(mapped.id)
        if existing and _phase3_content_is_usable(existing.content):
            already.append(existing)
            continue
        to_draft.append(mapped)
    return to_draft, already


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
    """Extract evidence citations from content. KB references removed - returns empty list."""
    # KB references are no longer included in proposals
    return []


def _plan_section_brief(state: DraftingGraphState, section_id: str) -> dict[str, Any] | None:
    plan = state.get("execution_plan") or {}
    writing = plan.get("writing") or {}
    plans = (writing.get("sectionPlans") or {}).get("plans") or []
    for item in plans:
        if isinstance(item, dict) and str(item.get("sectionId") or "") == section_id:
            return item
    return None


def _plan_retrieval_entry(state: DraftingGraphState, section_id: str) -> dict[str, Any] | None:
    plan = state.get("execution_plan") or {}
    writing = plan.get("writing") or {}
    entries = (writing.get("retrievalPlan") or {}).get("entries") or []
    for item in entries:
        if isinstance(item, dict) and str(item.get("sectionId") or "") == section_id:
            return item
    return None


_PLAN_DRIVEN_TITLE_HINTS = (
    "cover letter",
    "executive summary",
    "understanding",
    "challenge",
    "requirement",
    "methodology",
    "approach",
    "process",
    "timeline",
    "schedule",
    "work plan",
    "project plan",
    "budget",
    "pricing",
    "fees",
    "cost",
    "qualification",
    "relevant experience",
    "firm experience",
    "team experience",
    "past performance",
    "similar project",
)


_WHOLE_SECTION_VERIFY_RE = re.compile(
    r"^\[VERIFY:\s*Draft content for .+ — (?:insufficient evidence in corpus|writer returned empty prose)",
    re.I | re.S,
)


def _is_qualifications_narrative(title: str) -> bool:
    lower = (title or "").strip().lower()
    return any(
        hint in lower
        for hint in (
            "qualification",
            "relevant experience",
            "offeror qualification",
            "vendor qualification",
            "firm experience",
        )
    )


def _is_plan_driven_narrative(*, title: str, register: str) -> bool:
    if register != "narrative":
        return False
    lower = (title or "").strip().lower()
    if _is_qualifications_narrative(title):
        return True
    return any(hint in lower for hint in _PLAN_DRIVEN_TITLE_HINTS)


def _is_whole_section_verify_placeholder(content: str) -> bool:
    return bool(_WHOLE_SECTION_VERIFY_RE.match((content or "").strip()))


def _section_prose_missing(content: str) -> bool:
    stripped = (content or "").strip()
    return not stripped or _is_whole_section_verify_placeholder(stripped)


def _looks_truncated_prose(content: str) -> bool:
    """Detect mid-sentence cutoffs from max-output token limits."""
    stripped = (content or "").rstrip()
    if len(stripped) < 350:
        return False
    tail = stripped[-220:]
    if re.search(r'[.!?](?:\s|$|")', tail):
        return False
    if re.search(r"\]\s*$", stripped):
        return False
    return True


def _empty_draft_fallback(
    *,
    title: str,
    register: str,
    requirements: list[Any] | None,
    has_plan_context: bool,
) -> str:
    reqs = [str(r) for r in (requirements or [])[:3]]
    req_tail = f" Requirements: {'; '.join(reqs)}" if reqs else ""
    if _is_plan_driven_narrative(title=title, register=register) and has_plan_context:
        return (
            f"[VERIFY: Draft content for {title} — writer returned empty prose; "
            f"re-run Phase 3 for this section using plan/winning-pattern context.{req_tail}]"
        )
    return (
        f"[VERIFY: Draft content for {title} — "
        f"insufficient evidence in corpus.{req_tail}]"
    )


def _format_plan_context(state: DraftingGraphState, section_id: str) -> str:
    plan = state.get("execution_plan") or {}
    if not plan:
        return ""
    lines: list[str] = []
    memory = (plan.get("proposalMemory") or {}).get("facts") or {}
    if memory:
        lines.append("Proposal Memory (normalized facts — prefer these):")
        lines.append(json.dumps(memory, indent=2)[:3000])
    understanding = (plan.get("opportunity") or {}).get("understanding") or {}
    if isinstance(understanding, dict) and any(understanding.values()):
        lines.append("Opportunity Understanding (restate in zö voice; do not invent facts):")
        lines.append(json.dumps(understanding, indent=2)[:3000])
    brief = _plan_section_brief(state, section_id)
    if brief:
        lines.append("Section Strategy (explain the plan — do not invent methodology/budget):")
        lines.append(
            json.dumps(
                {
                    "purpose": brief.get("purpose"),
                    "keyMessages": brief.get("keyMessages"),
                    "writerInstructions": brief.get("writerInstructions"),
                    "successDefinition": brief.get("successDefinition"),
                    "wordBudget": brief.get("wordBudget"),
                    "tone": brief.get("tone"),
                },
                indent=2,
            )
        )
        winning_pattern = brief.get("winningPattern") or {}
        if isinstance(winning_pattern, dict) and any(winning_pattern.values()):
            lines.append(
                "Winning Pattern (structure and persuasion guidance only — "
                "Do not copy prior proposal prose):"
            )
            lines.append(json.dumps(winning_pattern, indent=2)[:3000])
    strategy = (plan.get("opportunity") or {}).get("strategy") or {}
    if strategy.get("winningTheme") or strategy.get("whyUs"):
        lines.append(
            "Opportunity strategy themes:\n"
            f"- winningTheme: {strategy.get('winningTheme')}\n"
            f"- whyUs: {strategy.get('whyUs')}"
        )
    methodology = (plan.get("delivery") or {}).get("methodology") or {}
    if isinstance(methodology, dict) and (methodology.get("phases") or methodology.get("confidence")):
        lines.append(
            "Delivery Methodology Plan (explain this structure in zö voice — do not invent phases):"
        )
        lines.append(json.dumps(methodology, indent=2)[:3000])
    budget_plan = (plan.get("delivery") or {}).get("budget") or {}
    if isinstance(budget_plan, dict) and any(budget_plan.values()):
        lines.append(
            "Delivery Budget Plan (use for Budget narrative — transparency/model/allocation; "
            "do not invent role-hour fee tables):"
        )
        lines.append(json.dumps(budget_plan, indent=2)[:3000])
    return "\n\n".join(lines)


async def _retry_plan_driven_section(
    section: dict[str, Any],
    state: DraftingGraphState,
    *,
    payload: dict[str, Any],
    max_tokens: int = 8192,
    reason: str = "empty",
) -> dict[str, Any] | None:
    """One focused retry when a plan-driven narrative section fails or truncates."""
    sid = str(section.get("id") or "")
    title = str(section.get("title") or sid)
    plan_ctx = str(payload.get("planContext") or _format_plan_context(state, sid)).strip()
    retry_user = (
        f"Client: {state.get('rfp_client')}\n"
        f"RFP: {state.get('rfp_title')}\n\n"
        f"Draft ONLY this narrative section now. Return non-empty prose.\n"
        f"Retry reason: {reason}.\n"
        "Use plan context, proof points, agency capabilities, and RFP requirements. "
        "Cite [E#] only if evidence is present.\n"
        "Do NOT return empty content. Do NOT return a whole-section VERIFY.\n"
        "For qualifications: use KB case studies and agency facts when present; "
        "otherwise write capability-aligned narrative from plan memory (no invented client names).\n\n"
        f"Plan context:\n{plan_ctx[:6000]}\n\n"
        f"Section payload:\n{json.dumps(payload, indent=2)[:5000]}\n\n"
        "Return JSON: {\"sections\":[{\"sectionId\":\""
        f"{sid}"
        "\",\"content\":\"full prose\",\"kbRefs\":[],\"designerNote\":null}]}"
    )
    try:
        raw, _provider = await chat_json_with_repair(
            [
                {"role": "system", "content": DRAFT_BATCH_PROMPT},
                {"role": "user", "content": retry_user},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )
    except LlmError as exc:
        logger.warning("Plan-driven retry failed for %s: %s", sid, exc)
        return None
    drafted = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(drafted, list):
        return None
    for item in drafted:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("sectionId") or item.get("id") or "").strip()
        if item_id == sid and str(item.get("content") or "").strip():
            logger.info("Plan-driven retry recovered empty section %s (%s)", sid, title)
            return item
    return None


async def _ensure_jit_evidence(
    state: DraftingGraphState,
    section_id: str,
) -> list[dict[str, Any]]:
    """JIT-retrieve for a section using retrievalPlan; merge into state corpus."""
    corpus = list(state.get("evidence_corpus") or [])
    tagged = _evidence_for_section(section_id, corpus)

    entry_raw = _plan_retrieval_entry(state, section_id)
    section_title = ""
    for section in state.get("rfp_sections") or []:
        if str(section.get("id") or "") == section_id:
            section_title = str(section.get("title") or "")
            break
    is_budget_section = any(
        k in section_title.lower() for k in ("budget", "pricing", "fees", "cost")
    )

    # Budget narrative must ground in 00_Guide_Pricing — always supplement.
    if is_budget_section:
        from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
        from app.services.proposal_intelligence.schemas import RetrievalEntry

        pricing_entry = RetrievalEntry.model_validate(
            {
                "sectionId": section_id,
                "requiredAssets": ["00_Guide_Pricing pricing guide"],
                "queries": [
                    "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management",
                    "00_Guide_Pricing transparent compensation pass-through media markup agency fees",
                ],
                "priority": "required",
                "expectedSources": ["pricing"],
                "whyNeeded": "Budget narrative must follow pricing guide rules",
            }
        )
        start = len(corpus) + 1
        items = await retrieve_for_section(
            pricing_entry,
            rfp_client=str(state.get("rfp_client") or ""),
            start_index=start,
        )
        for item in items:
            dumped = item.model_dump(by_alias=True)
            dumped["sectionIds"] = list(
                dict.fromkeys([*(dumped.get("sectionIds") or []), section_id])
            )
            corpus.append(dumped)
        state["evidence_corpus"] = corpus
        tagged = _evidence_for_section(section_id, corpus)
        if tagged:
            return tagged

    if tagged:
        return tagged

    if not entry_raw:
        return corpus[:12]

    from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
    from app.services.proposal_intelligence.schemas import RetrievalEntry

    try:
        entry = RetrievalEntry.model_validate(entry_raw)
    except Exception:
        return corpus[:12]

    start = len(corpus) + 1
    items = await retrieve_for_section(
        entry,
        rfp_client=str(state.get("rfp_client") or ""),
        start_index=start,
    )
    for item in items:
        corpus.append(item.model_dump(by_alias=True))
    state["evidence_corpus"] = corpus
    return _evidence_for_section(section_id, corpus) or corpus[:12]


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
                        "content": SECTION_DRAFT_FAILURE_PLACEHOLDER,
                        "status": "outline",
                        "kbRefs": [],
                    }
                )
                logger.warning(
                    "Phase 3 section %s draft failed after JSON repair: %s",
                    sid,
                    single_exc,
                )
        return merged, provider


async def _draft_batch_once(
    batch: list[dict[str, Any]],
    state: DraftingGraphState,
) -> tuple[list[dict[str, Any]], str]:
    batch_payload: list[dict[str, Any]] = []

    for section in batch:
        sid = str(section.get("id") or "")
        title = str(section.get("title") or sid)
        log_intel_event(
            "SECTION_GENERATE_NEXT",
            rfp_id=state.get("rfp_id"),
            section_id=sid,
            title=title,
            phase="phase-3",
        )
        evidence = await _ensure_jit_evidence(state, sid)
        zo_mode = str(section.get("zoMode") or section.get("zo_mode") or "write")
        register = classify_section_register(
            section_id=sid,
            title=title,
            zo_mode=zo_mode,
        )
        brief = _plan_section_brief(state, sid)
        word_target = (
            int(brief.get("wordBudget") or 0)
            if brief and brief.get("wordBudget")
            else _word_target(section)
        )
        batch_payload.append(
            {
                "sectionId": sid,
                "title": title,
                "register": register,
                "requirements": section.get("requirements") or [],
                "zoMode": zo_mode,
                "wordTarget": word_target or _word_target(section),
                "uncoveredRequirements": section.get("uncoveredRequirements")
                or section.get("uncovered_requirements")
                or [],
                "evidence": _format_evidence_block(evidence),
                "planContext": _format_plan_context(state, sid),
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

    from app.services.proposal_section_dedup import (
        format_anti_duplication_rules,
        format_prior_sections_block,
    )

    user_content += f"{format_anti_duplication_rules()}\n\n"
    prior = state.get("drafted_sections") or []
    batch_ids = {
        str(s.get("id") or "") for s in batch if s.get("id")
    }
    prior_block = format_prior_sections_block(prior, exclude_ids=batch_ids)
    if prior_block:
        user_content += f"{prior_block}\n\n"

    from app.services.proposal_manuscript_locks import format_manuscript_locks_block
    from app.models.proposal import ManuscriptLocks

    locks_raw = state.get("manuscript_locks")
    locks = None
    if isinstance(locks_raw, ManuscriptLocks):
        locks = locks_raw
    elif isinstance(locks_raw, dict):
        try:
            locks = ManuscriptLocks.model_validate(locks_raw)
        except Exception:
            locks = None
    locks_block = format_manuscript_locks_block(locks)
    if locks_block:
        user_content += f"{locks_block}\n\n"

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

    for payload in batch_payload:
        plan_ctx = str(payload.get("planContext") or "").strip()
        if plan_ctx:
            user_content += (
                f"Execution plan context for {payload.get('sectionId')}:\n{plan_ctx}\n\n"
            )
        if _is_plan_driven_narrative(
            title=str(payload.get("title") or ""),
            register=str(payload.get("register") or ""),
        ):
            evidence_text = str(payload.get("evidence") or "")
            thin_evidence = (
                not evidence_text.strip()
                or "No evidence items tagged" in evidence_text
            )
            if thin_evidence:
                user_content += (
                    f"IMPORTANT for {payload.get('sectionId')} ({payload.get('title')}): "
                    "Evidence is thin or empty. Still draft full submission-ready narrative "
                    "from Opportunity Understanding, Section Strategy, Winning Pattern, "
                    "RFP requirements, and Proposal Memory. Do not return an empty content "
                    "field or a whole-section VERIFY about insufficient evidence.\n\n"
                )
            if _is_qualifications_narrative(str(payload.get("title") or "")):
                user_content += (
                    f"QUALIFICATIONS SECTION {payload.get('sectionId')}: "
                    "Write full experience narrative using retrieved case studies, references, "
                    "and agency credentials when present. If geo-specific case studies are "
                    "missing, describe transferable place-branding / economic development "
                    "capabilities without inventing false project names or metrics.\n\n"
                )
            title_lower = str(payload.get("title") or "").lower()
            if any(k in title_lower for k in ("budget", "pricing", "fees", "cost")):
                user_content += (
                    f"BUDGET NARRATIVE REQUIRED for {payload.get('sectionId')}: "
                    "Write transparency, pass-through media buys, compensation model, and "
                    "allocation rationale using RFP spend figures from requirements/plan. "
                    "Do not invent agency fee line-item tables. Do not return empty content.\n\n"
                )

    user_content += f"Sections to draft:\n{json.dumps(batch_payload, indent=2)}"

    draft_max_tokens = 12_288 if any(
        _is_plan_driven_narrative(
            title=str(s.get("title") or ""),
            register=classify_section_register(
                section_id=str(s.get("id") or ""),
                title=str(s.get("title") or ""),
                zo_mode=str(s.get("zoMode") or s.get("zo_mode") or "write"),
            ),
        )
        for s in batch
    ) else 8192

    async with state["llm_semaphore"]:
        raw, provider = await chat_json_with_repair(
            [
                {"role": "system", "content": DRAFT_BATCH_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=draft_max_tokens,
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

    payload_by_id = {
        str(p.get("sectionId") or ""): p for p in batch_payload if p.get("sectionId")
    }

    for section in batch:
        sid = str(section.get("id") or "")
        item = drafted_by_id.get(sid, {})
        content = str(item.get("content", "")).strip()
        zo_mode = str(section.get("zoMode") or section.get("zo_mode") or "write")
        title = str(section.get("title") or sid)
        register = classify_section_register(
            section_id=sid,
            title=title,
            zo_mode=zo_mode,
        )
        section_payload = payload_by_id.get(sid) or {
            "sectionId": sid,
            "title": title,
            "register": register,
            "requirements": section.get("requirements") or [],
            "wordTarget": _word_target(section),
            "evidence": "(retry — use plan context)",
            "planContext": _format_plan_context(state, sid),
        }
        if _section_prose_missing(content) and _is_plan_driven_narrative(
            title=title, register=register
        ):
            logger.warning(
                "Phase 3 empty prose for plan-driven section %s (%s) — retrying once",
                sid,
                title,
            )
            retried = await _retry_plan_driven_section(
                section,
                state,
                payload=section_payload,
                reason="empty or verify-only",
            )
            if retried:
                item = retried
                content = str(item.get("content") or "").strip()
        elif _looks_truncated_prose(content) and _is_plan_driven_narrative(
            title=title, register=register
        ):
            logger.warning(
                "Phase 3 truncated prose for section %s (%s) — retrying with higher token limit",
                sid,
                title,
            )
            retried = await _retry_plan_driven_section(
                section,
                state,
                payload=section_payload,
                max_tokens=16_384,
                reason="previous draft ended mid-sentence (output limit)",
            )
            if retried:
                candidate = str(retried.get("content") or "").strip()
                if candidate and len(candidate) > len(content):
                    item = retried
                    content = candidate
        if _section_prose_missing(content):
            plan_ctx = _format_plan_context(state, sid)
            content = _empty_draft_fallback(
                title=title,
                register=register,
                requirements=section.get("requirements") or [],
                has_plan_context=bool(plan_ctx.strip()),
            )
        else:
            content = enforce_narrative_voice(
                content,
                section_id=sid,
                title=title,
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
    sections = order_sections_for_phase3_draft(sections)
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
        rfp_id = str(state.get("rfp_id") or "")
        if rfp_id:
            from app.services.proposal_generation_cancel import check_generation_cancelled

            await check_generation_cancelled(rfp_id)
        if batch and rfp_id:
            sec = batch[0]
            sec_title = str(sec.get("title") or sec.get("id") or "Section")
            from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

            await record_pipeline_activity(
                rfp_id,
                label=f"Drafting: {sec_title}",
                detail="LLM writing this RFP tab (not a context limit — one section per request).",
                step_index=index,
                step_total=len(batches),
            )
        try:
            # Pass already-drafted sections so each batch avoids repeating them
            batch_state = {
                **state,
                "drafted_sections": all_drafted,
            }
            batch_results, batch_provider = await _draft_batch(batch, batch_state)
            all_drafted.extend(batch_results)
            provider = batch_provider
            logger.info(
                "Phase 3 batch %d/%d complete for %s (%d sections)",
                index,
                len(batches),
                state.get("rfp_id"),
                len(batch_results),
            )
            callback = _SECTION_DRAFT_CALLBACKS.get(str(state.get("rfp_id") or ""))
            if callback:
                drafted_sections = [
                    ProposalSection.model_validate(item) for item in all_drafted
                ]
                await callback(drafted_sections, provider)
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
                        "content": SECTION_DRAFT_FAILURE_PLACEHOLDER,
                        "status": "outline",
                        "kbRefs": [],
                    }
                )
            logger.warning(
                "Phase 3 batch %d failed for %s after repair: %s",
                index,
                state.get("rfp_id"),
                exc,
            )

    return {
        "drafted_sections": all_drafted,
        "provider": provider,
        "evidence_corpus": state.get("evidence_corpus") or [],
    }


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
    manuscript_locks: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    on_sections_drafted: SectionDraftedCallback | None = None,
) -> tuple[list[ProposalSection], str, list[EvidenceItem]]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    plan_dict = execution_plan
    if plan_dict is not None and hasattr(plan_dict, "model_dump"):
        plan_dict = plan_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    locks_dict = manuscript_locks
    if locks_dict is not None and hasattr(locks_dict, "model_dump"):
        locks_dict = locks_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    initial: DraftingGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "rfp_sections": [s.model_dump(by_alias=True) for s in rfp_sections],
        "evidence_corpus": [e.model_dump(by_alias=True) for e in evidence_corpus],
        "execution_plan": plan_dict if isinstance(plan_dict, dict) else None,
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
        "manuscript_locks": locks_dict if isinstance(locks_dict, dict) else None,
        "drafted_sections": [],
        "llm_semaphore": asyncio.Semaphore(LLM_CONCURRENCY),
    }

    if on_sections_drafted:
        _SECTION_DRAFT_CALLBACKS[rfp_id] = on_sections_drafted

    logger.info("Phase 3 drafting graph starting for rfp_id=%s", rfp_id)
    try:
        final = await _DRAFTING_GRAPH.ainvoke(initial)
    finally:
        if on_sections_drafted:
            _SECTION_DRAFT_CALLBACKS.pop(rfp_id, None)

    if final.get("error"):
        raise LlmError(str(final["error"]), status_code=400)

    drafted = [
        ProposalSection.model_validate(item) for item in (final.get("drafted_sections") or [])
    ]
    provider = str(final.get("provider") or _provider_name())
    jit_corpus = [
        EvidenceItem.model_validate(item) for item in (final.get("evidence_corpus") or [])
    ]
    logger.info(
        "Phase 3 drafting complete for %s: %d sections, %d evidence items",
        rfp_id,
        len(drafted),
        len(jit_corpus),
    )
    return drafted, provider, jit_corpus


async def draft_single_rfp_section_phase3(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    section: RfpSectionMap,
    evidence_corpus: list[EvidenceItem],
    brand_voice: ProposalBrandVoice | None,
    zo_template_sections: list[ProposalSection] | None = None,
    writing_avoidances: list[str] | None = None,
    loss_lessons: list[LossLesson] | None = None,
    proof_points: list | None = None,
    manuscript_locks: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    rewrite_brief: str = "",
) -> tuple[ProposalSection, str, list[EvidenceItem]]:
    """Phase 3 drafting path for exactly one RFP-mapped section (Senior Editor tickets)."""
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    plan_dict = execution_plan
    if plan_dict is not None and hasattr(plan_dict, "model_dump"):
        plan_dict = plan_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    locks_dict = manuscript_locks
    if locks_dict is not None and hasattr(locks_dict, "model_dump"):
        locks_dict = locks_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    section_dump = section.model_dump(by_alias=True)
    if rewrite_brief.strip():
        # Inject Senior Editor brief into uncoveredRequirements so the batch prompt sees it.
        extra = list(section_dump.get("uncoveredRequirements") or [])
        extra.append(f"Senior Editor rewrite brief: {rewrite_brief.strip()}")
        section_dump["uncoveredRequirements"] = extra

    state: DraftingGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "rfp_sections": [section_dump],
        "evidence_corpus": [e.model_dump(by_alias=True) for e in evidence_corpus],
        "execution_plan": plan_dict if isinstance(plan_dict, dict) else None,
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
        "manuscript_locks": locks_dict if isinstance(locks_dict, dict) else None,
        "drafted_sections": [],
        "llm_semaphore": asyncio.Semaphore(LLM_CONCURRENCY),
    }

    results, provider = await _draft_batch([section_dump], state)
    if not results:
        raise LlmError(f"Phase 3 single-section draft returned empty for {section.id}", status_code=422)
    drafted = ProposalSection.model_validate(results[0])
    jit_corpus = [
        EvidenceItem.model_validate(item) for item in (state.get("evidence_corpus") or [])
    ]
    logger.info(
        "Phase 3 single-section draft for %s / %s (%d chars)",
        rfp_id,
        section.id,
        len(drafted.content or ""),
    )
    return drafted, provider, jit_corpus
