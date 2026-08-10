"""LangChain agent profiles — one distinct agent per proposal edit loop."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm import LlmError, LlmTier, _fireworks_key, chat_json
from app.services.proposal_langchain import (
    _provider_name,
    _use_fireworks_primary,
    build_proposal_tools,
    get_chat_model,
    run_tool_agent_loop,
)

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    RESEARCH = "research"
    SENIOR_EDITOR = "senior_editor"
    SECTION_REPAIR = "section_repair"
    USER_REVISE = "user_revise"
    SURGICAL_FIX = "surgical_fix"
    QUERY_PLANNER = "query_planner"


@dataclass(frozen=True)
class AgentProfile:
    role: AgentRole
    label: str
    temperature: float
    max_tokens: int
    max_tool_rounds: int
    system_prompt: str
    tier: LlmTier = "heavy"
    # Stage-map key — see _QUALITY_EXACT / _MECHANICAL_EXACT in llm_routing.py.
    # Without it this layer falls through to the cheapest provider.
    node_name: str = ""


SENIOR_EDITOR_SYSTEM = """You are zö agency's Senior Proposal Editor (manuscript director).
You do NOT fill [VERIFY] tags or invent KB facts — the KB fact-checker owns facts.
You do NOT rewrite every section or hunt grammar for its own sake.

Your ONLY jobs for ONE pass over the proposal:
1. DEDUPE — Scan EVERY section.
   a) If Who We Are / brand story / FEIN / full bios / full case studies / budget tables are
      re-copied into a section that has a different job, emit a dedupeTicket with trimGuidance:
      keep what THAT section needs + one short cross-ref — remove the unnecessary duplicate.
      Do NOT blank required content.
   b) If TWO RFP tabs are near-duplicates of each other (same ask / same proof rewritten —
      e.g. "Relevant Experience" vs "Successful Campaigns" both rewriting the same case study),
      emit a deleteSectionTicket for the weaker/later tab with keepSectionId pointing at the
      tab that should remain. NEVER delete Sections 1.1–1.5, bio cards, or Our Work cards.
      NEVER delete References, Compensation, Communication/Collaboration, Reports, Schedules,
      Crisis, Workflow, or undrafted empty tabs. Prefer deleting content clones over leaving
      the designer to cut pages.
2. RFP COVERAGE — For each mapped RFP requirement, check whether the manuscript covers it.
   If unmet, emit a coverageTicket with unmetRequirements and a rewriteBrief for the writer.
3. GOV / BUYER COMPLIANCE — Flag missing mandatory public-sector items THIS RFP demands
   (addenda ack, non-collusion, insurance/COI, W-9, authorized signature, pricing form,
   MCCS/state terms, validity period, tax-exempt handling, required exhibits). Emit a
   complianceTicket with policyOrGuideline + rewriteBrief. Do NOT invent policies the RFP
   never mentions. Do NOT paste generic FAR/GSA boilerplate unprompted by THIS RFP.
4. Do NOT rewrite section prose yourself. Do NOT emit tickets for style/tone polish.
5. Return ONLY JSON:
{"deleteSectionTickets":[{"sectionId":"...","keepSectionId":"...","reason":"..."}],
 "dedupeTickets":[{"sectionId":"...","keepHomeSectionId":"...","trimGuidance":"..."}],
 "coverageTickets":[{"sectionId":"...","unmetRequirements":["..."],"rewriteBrief":"..."}],
 "complianceTickets":[{"sectionId":"...","policyOrGuideline":"...","rewriteBrief":"..."}],
 "notes":[]}
If nothing to fix: empty ticket arrays.
"""

SECTION_REPAIR_SYSTEM = """You are zö agency's Section Repair agent (self-edit loop after Phase 3).
Your job: search tools, then produce ONE complete section patch.

CRITICAL TOOL SPLIT:
- KB (search_knowledge_base / case studies / bios / master template) = ONLY zö materials.
  Query what zö can provide for the RFP theme (sector, deliverables, audience type).
  NEVER search KB with the RFP buyer/prospect as the subject — they are not in the KB.
- Buyer requirements (reference format, scoring, methodology demands, forms) = search_rfp_requirements.

Rules:
1. Call tools until you have enough facts — do not stop after one search.
2. Remove [VERIFY] stubs when evidence supports real prose. Cite [E#] when using corpus IDs provided.
3. First person we/our in narrative sections — never "The Vendor". Never use "we" as a possessive ("of we", "across we").
4. Use ONLY verified KB and RFP facts. Do not invent clients, contacts, or metrics.
5. Address every RFP requirement listed for this section.
6. SUBMISSION POLISH tasks: fix ONLY the listed defects; preserve all other sentences verbatim.
7. Grammar: "We were established …, and is …" must become "and are …" or be rephrased.
8. Subcontractors: if cost proposal lists translation partners, Company Background must align — zö self-performs marketing/communications; translation partners are scoped separately.
9. RFP compliance: reference contacts with phones and emails, workforce diversity %, budget hours table, PSA acks — from KB only; never defer to unnamed attachments or "upon request".
10. BUDGET / COST / FEES / PRICING sections (critical):
   - NEVER search the general knowledge base for this client's rates, hours, or fee totals — new RFPs have no client-specific pricing in KB.
   - ALWAYS call search_rfp_requirements first for budget ceiling, cost scoring, quote/fee form requirements.
   - THEN call search_pricing_guide to get 00_Guide_Pricing Low/Average/High tiers and menu rates.
   - Pick ONE tier deliberately from RFP budget pressure + evaluation weight on cost, then build fees from the guide.
   - Never invent dollar amounts. Use [VERIFY: …] when guide/RFP lacks a figure. Never put a phone number in a Fee column.
11. MWBE and Personnel must use the same workforce percentages — align to one HR-verified figure.
12. ANTI-DUPLICATION: This section has ONE job. Do not re-paste company bio, full bios, or full case studies owned by other sections. One short cross-reference is OK — then add NEW detail only. Prefer concise prose.
13. LENGTH & FORMAT: Stay at or under the Word target. Prefer short paragraphs, markdown bullets, and markdown tables for phases/process/cadence. Set designerNote (or inline [DESIGNER NOTE: …]) when a table/timeline/swimlane would help evaluators.
14. When done researching, respond with ONLY JSON:
{"content":"full section prose","kbRefs":["E1"],"designerNote":"layout hint or null"}"""

USER_REVISE_SYSTEM = """You are zö agency's User Revise agent (editor chat / Revise content flow).
The user gave explicit feedback. Search tools only as needed, then update ONE section.

CRITICAL TOOL SPLIT:
- KB tools = zö facts only (capabilities, case studies, bios, companyfacts). Query themes like
  "zö agency higher-ed community college marketing case studies 03_CS" — NEVER
  "KVCC … marketing" (buyer is not in the KB; that wastes tokens).
- What the buyer demands (reference relevance rules, section scoring, forms) = search_rfp_requirements.

Rules:
1. FIRST understand the user's ask. Prefer the SMALLEST change that fully satisfies it.
2. Do NOT rewrite unrelated paragraphs, add new intros, or expand the section unless the user asked for that.
3. Call KB tools only when the ask needs zö facts missing from the draft; call search_rfp_requirements for buyer rules.
4. Never return the same [VERIFY] placeholder if tools found support for that field.
5. PRESERVE zö BRAND VOICE: first person we/our, warm, confident, proof-led — never flatten into generic consultant prose.
6. Budget/fee edits (critical):
   - Do NOT query general KB for this client's budget/hours/rates — KB has no new-client pricing.
   - Use search_rfp_requirements for budget thresholds / cost criteria, then search_pricing_guide for 00_Guide_Pricing tiers.
   - Choose Low/Average/High from RFP + guide; never invent numbers or reverse-engineer totals.
   - Refuse invented dollars; flag out-of-guide scope with [PRICING FLAG: … — Sonja review required]. One-time setup lines must not be ×12 without a monthly guide line.
7. Reference edits: full contact block (name, title, phone, email) — never defer to "on request".
   Clean/filter references with search_case_studies + RFP reference rules — not by searching the buyer's name in KB.
8. NEVER put citation markers like [E1], [E14], or **[E3]** in the prose — client-facing text only.
9. LENGTH & FORMAT: Stay at or under Word target when provided. Prefer bullets and markdown tables for process/phases. Add designerNote / [DESIGNER NOTE: …] when layout helps.
10. Return ONLY JSON: {"content":"...","kbRefs":[],"designerNote":"layout hint or null"}"""

SURGICAL_FIX_SYSTEM = """You are zö agency's Surgical Fix agent (pre-submit review auto-fix).
Patch ONE section to clear listed review issues — minimal diff, preserve strong prose.

Rules:
1. Search KB tools only when needed to resolve [VERIFY] or missing zö facts. Never search KB for the RFP buyer by name.
2. For budget/fee issues: search_rfp_requirements + search_pricing_guide only — never invent client prices from general KB.
3. Fix wrong-client names, voice issues, and placeholders from the issues list.
4. Do NOT invent facts. Do NOT add marketing fluff to procurement/form sections.
5. Change only what the issues require.
6. Return ONLY JSON: {"content":"full updated section text","kbRefs":[]}"""

QUERY_PLANNER_SYSTEM = """You are zö agency's Query Planner agent.

The knowledge base is ONLY about zö agency — never about the RFP buyer/prospect.
FIRST understand the task. Map it to RFP themes + [VERIFY] gaps. Then plan 2-4 NEW Supermemory queries
about what zö can provide (not who the buyer is).

Rules:
- Prior queries already ran — never repeat them.
- NEVER put the RFP client/buyer name as the search subject (e.g. "KVCC … college marketing").
  Frame as: "zö agency [sector/theme] [capability] 03_CS / 01 companyfacts / 04 bio".
- Buyer requirements are NOT planned as KB queries — those use the RFP tool at edit time.
- Use hints: 01 companyfacts, 02 master template, 03_CS case studies, 04 bio, certifications, org chart, references.
- When [VERIFY] gaps are listed, dedicate a query to each missing zö field.
- For health/coalition/stigma RFPs, include Recovery Network of Oregon (RNO) / Oregon Recovers when the section is experience, references, or case studies.
- Do NOT invent queries that imply E-Verify is confirmed — search 01_companyfacts only; enrollment stays VERIFY unless facts explicitly confirm.
- BUDGET / COST / FEES / PRICING sections: do NOT plan queries like "<client> marketing plan budget hours rate".
  Plan ONLY 00_Guide_Pricing queries (tier Low Average High, menu rates, PM floor) — RFP budget ceilings are read via the RFP tool, not Supermemory client docs.
- Each non-budget query MUST include "zö agency" + the specific fact + a doc-type hint.
- Avoid vague mash queries like "methodology won_proposals" alone.

Return ONLY JSON: {"queries":["query 1","query 2","query 3","query 4"]}"""

AGENT_PROFILES: dict[AgentRole, AgentProfile] = {
    AgentRole.SENIOR_EDITOR: AgentProfile(
        role=AgentRole.SENIOR_EDITOR,
        label="Senior Proposal Editor",
        temperature=0.15,
        max_tokens=4096,
        max_tool_rounds=0,
        system_prompt=SENIOR_EDITOR_SYSTEM,
        tier="heavy",
        node_name="senior_editor",
    ),
    AgentRole.SECTION_REPAIR: AgentProfile(
        role=AgentRole.SECTION_REPAIR,
        label="Section Repair",
        temperature=0.3,
        max_tokens=4096,
        max_tool_rounds=2,
        system_prompt=SECTION_REPAIR_SYSTEM,
        tier="heavy",
        node_name="section_repair",
    ),
    AgentRole.USER_REVISE: AgentProfile(
        role=AgentRole.USER_REVISE,
        label="User Revise",
        temperature=0.35,
        max_tokens=4096,
        max_tool_rounds=2,
        system_prompt=USER_REVISE_SYSTEM,
        tier="heavy",
        node_name="user_revise",
    ),
    AgentRole.SURGICAL_FIX: AgentProfile(
        role=AgentRole.SURGICAL_FIX,
        label="Surgical Fix",
        temperature=0.15,
        max_tokens=4096,
        max_tool_rounds=3,
        system_prompt=SURGICAL_FIX_SYSTEM,
        tier="heavy",
        node_name="surgical_fix",
    ),
    AgentRole.QUERY_PLANNER: AgentProfile(
        role=AgentRole.QUERY_PLANNER,
        label="Query Planner",
        temperature=0.35,
        max_tokens=1024,
        max_tool_rounds=0,
        system_prompt=QUERY_PLANNER_SYSTEM,
        tier="light",
        node_name="query_planner",
    ),
}


def get_profile(role: AgentRole) -> AgentProfile:
    return AGENT_PROFILES[role]


_CONTENT_KEY_RE = re.compile(
    r'"(?:content|sectionContent|section_content)"\s*:\s*"',
    re.IGNORECASE,
)


def _unescape_json_fragment(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return (
            raw.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def _salvage_content_string(text: str) -> str:
    """Pull section prose from agent output when JSON parsing leaves content empty."""
    match = _CONTENT_KEY_RE.search(text)
    if not match:
        return ""
    chunk = text[match.end() :]
    buf: list[str] = []
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if ch == "\\" and i + 1 < len(chunk):
            buf.append(chunk[i : i + 2])
            i += 2
            continue
        if ch == '"':
            return _unescape_json_fragment("".join(buf)).strip()
        buf.append(ch)
        i += 1
    return _unescape_json_fragment("".join(buf)).strip()


def content_from_agent_payload(parsed: dict[str, Any], raw_text: str = "") -> str:
    """Normalize agent JSON to section prose."""
    from app.services.proposal_manuscript import strip_evidence_citation_markers

    for key in ("content", "sectionContent", "section_content", "text", "prose"):
        val = parsed.get(key)
        if isinstance(val, str) and val.strip():
            return strip_evidence_citation_markers(val.strip())
    salvaged = _salvage_content_string(raw_text)
    if salvaged:
        return strip_evidence_citation_markers(salvaged)
    stripped = raw_text.strip()
    if stripped and not stripped.startswith("{"):
        return strip_evidence_citation_markers(stripped)
    return ""


async def _parse_json_from_agent_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            content = content_from_agent_payload(parsed, stripped)
            if content and not str(parsed.get("content") or "").strip():
                parsed = {**parsed, "content": content}
            return parsed
    except json.JSONDecodeError:
        pass
    structured, _ = await chat_json(
        [
            {"role": "system", "content": "Extract JSON object from agent output. Return only JSON."},
            {"role": "user", "content": text[:12000]},
        ],
        max_tokens=4096,
        temperature=0.0,
    )
    if isinstance(structured, dict):
        content = content_from_agent_payload(structured, text)
        if content and not str(structured.get("content") or "").strip():
            structured = {**structured, "content": content}
        return structured
    salvaged = _salvage_content_string(text)
    if salvaged:
        return {"content": salvaged, "kbRefs": []}
    return {}


async def run_json_agent(
    role: AgentRole,
    user_content: str,
) -> tuple[dict[str, Any], str]:
    """Single-turn LangChain agent (no tools) — senior editor, query planner."""
    profile = get_profile(role)
    # Let the stage map decide rather than forcing Fireworks for every role:
    # quality roles (senior editor) must not be served by the economy model
    # when a better provider is configured. The retry below still falls back.
    force_fireworks = False

    async def _invoke(*, fireworks: bool) -> dict[str, Any]:
        llm = get_chat_model(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            force_fireworks=fireworks,
            tier=profile.tier,
            node_name=profile.node_name,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=profile.system_prompt),
                HumanMessage(content=user_content),
            ]
        )
        content = response.content
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return await _parse_json_from_agent_text(str(content))

    try:
        parsed = await _invoke(fireworks=force_fireworks)
    except Exception as exc:
        if _fireworks_key() and not force_fireworks:
            logger.warning("%s JSON agent failed (%s) — retrying Fireworks", profile.label, exc)
            parsed = await _invoke(fireworks=True)
            return parsed, "fireworks"
        raise

    return parsed, _provider_name(force_fireworks=force_fireworks)


async def run_tool_json_agent(
    *,
    role: AgentRole,
    rfp_id: str,
    title: str,
    client: str,
    user_content: str,
    sector: str = "",
) -> tuple[dict[str, Any], str, list[str]]:
    """Multi-turn LangChain agent with KB tools — repair, revise, surgical fix."""
    profile = get_profile(role)
    tools = build_proposal_tools(rfp_id, title, client, sector=sector)
    final_text, provider, tool_log = await run_tool_agent_loop(
        system_prompt=profile.system_prompt,
        user_content=user_content,
        tools=tools,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        max_rounds=profile.max_tool_rounds,
        agent_label=profile.label,
        rfp_id=rfp_id,
        tier=profile.tier,
        node_name=profile.node_name,
    )
    parsed = await _parse_json_from_agent_text(final_text)
    if not str(parsed.get("content") or "").strip():
        logger.warning(
            "%s agent empty content for %s after %d tool call(s) (final_chars=%d)",
            profile.label,
            rfp_id,
            len(tool_log),
            len(final_text),
        )
    return parsed, provider, tool_log


async def senior_editor_emit_tickets(
    *,
    rfp_client: str,
    rfp_title: str,
    manuscript_digest: str,
    requirements_by_section: dict[str, list[str]],
) -> dict[str, Any]:
    """Senior Editor: emit dedupe + coverage tickets (no prose rewrite, no fact fill)."""
    req_lines: list[str] = []
    for sid, reqs in requirements_by_section.items():
        if not reqs:
            continue
        req_lines.append(f"{sid}:")
        req_lines.extend(f"  - {r}" for r in reqs[:12])
    user_content = (
        f"Client: {rfp_client}\nRFP: {rfp_title}\n\n"
        f"Mapped requirements by section:\n"
        + ("\n".join(req_lines) if req_lines else "(none mapped)")
        + f"\n\nProposal manuscript digest:\n{manuscript_digest[:40_000]}"
    )
    try:
        raw, _ = await run_json_agent(AgentRole.SENIOR_EDITOR, user_content)
        deletes = (
            raw.get("deleteSectionTickets")
            if isinstance(raw.get("deleteSectionTickets"), list)
            else []
        )
        dedupe = raw.get("dedupeTickets") if isinstance(raw.get("dedupeTickets"), list) else []
        coverage = (
            raw.get("coverageTickets") if isinstance(raw.get("coverageTickets"), list) else []
        )
        compliance = (
            raw.get("complianceTickets")
            if isinstance(raw.get("complianceTickets"), list)
            else []
        )
        notes = raw.get("notes") if isinstance(raw.get("notes"), list) else []
        return {
            "deleteSectionTickets": [t for t in deletes if isinstance(t, dict)],
            "dedupeTickets": [t for t in dedupe if isinstance(t, dict)],
            "coverageTickets": [t for t in coverage if isinstance(t, dict)],
            "complianceTickets": [t for t in compliance if isinstance(t, dict)],
            "notes": [str(n) for n in notes if str(n).strip()],
        }
    except (LlmError, Exception) as exc:
        logger.warning("Senior editor ticket pass failed: %s", exc)
        return {
            "deleteSectionTickets": [],
            "dedupeTickets": [],
            "coverageTickets": [],
            "complianceTickets": [],
            "notes": [str(exc)],
        }


async def senior_editor_patch_instructions(
    *,
    rfp_id: str,
    section_title: str,
    section_content: str,
    word_target: int,
    rfp_client: str,
    rfp_title: str,
    requirements: list[str] | None = None,
) -> str:
    """Backward-compat: fold one section into ticket brief for Section Repair fallback."""
    del rfp_id, word_target  # unused — tickets use manuscript-level pass
    tickets = await senior_editor_emit_tickets(
        rfp_client=rfp_client,
        rfp_title=rfp_title,
        manuscript_digest=f"### {section_title}\n{section_content[:8000]}",
        requirements_by_section={section_title: list(requirements or [])},
    )
    parts: list[str] = []
    for t in tickets.get("coverageTickets") or []:
        brief = str(t.get("rewriteBrief") or "").strip()
        unmet = t.get("unmetRequirements") or []
        if brief:
            parts.append(brief)
        if isinstance(unmet, list) and unmet:
            parts.append("Unmet: " + "; ".join(str(u) for u in unmet[:8]))
    for t in tickets.get("complianceTickets") or []:
        brief = str(t.get("rewriteBrief") or "").strip()
        policy = str(t.get("policyOrGuideline") or "").strip()
        if policy:
            parts.append(f"Compliance: {policy}")
        if brief:
            parts.append(brief)
    for t in tickets.get("dedupeTickets") or []:
        guide = str(t.get("trimGuidance") or "").strip()
        if guide:
            parts.append(f"Dedupe: {guide}")
    return "\n".join(parts).strip()


async def plan_section_queries_agent(
    *,
    role: Literal[AgentRole.SECTION_REPAIR, AgentRole.USER_REVISE, AgentRole.QUERY_PLANNER],
    rfp_client: str,
    rfp_sector: str,
    section_title: str,
    requirements: list[str],
    retrieval_focus: list[str],
    prior_queries: list[str],
    user_message: str,
    current_content: str,
    rfp_title: str = "",
) -> list[str]:
    title_cf = (section_title or "").casefold()
    ask_cf = (user_message or "").casefold()
    is_budget = any(
        k in title_cf or k in ask_cf
        for k in ("budget", "pricing", "cost of", "fee", "compensation", "cost proposal")
    )
    if is_budget:
        # Never plan client-specific budget KB queries — pricing lives in the guide + RFP.
        guide_queries = [
            "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management",
            "00_Guide_Pricing 9.1 9.2 Project Management 5-8 percent floor Average tier",
            "00_Guide_Pricing transparent compensation pass-through agency fees qualifying language",
        ]
        used = {q.strip().lower() for q in prior_queries}
        return [q for q in guide_queries if q.lower() not in used][:4]

    try:
        raw, _ = await run_json_agent(
            AgentRole.QUERY_PLANNER,
            (
                f"Agent context: {role.value}\n"
                f"Client: {rfp_client}\nSector: {rfp_sector}\n"
                f"Section: {section_title}\n"
                f"Requirements: {requirements}\n"
                f"Retrieval focus: {retrieval_focus}\n"
                f"Prior queries (DO NOT repeat):\n"
                + "\n".join(f"- {q}" for q in prior_queries)
                + f"\n\nTask / user feedback:\n{user_message}\n\n"
                f"Current draft excerpt:\n{current_content[:2000]}"
            ),
        )
        queries = raw.get("queries", [])
        if not isinstance(queries, list):
            return []
        used = {q.strip().lower() for q in prior_queries}
        cleaned: list[str] = []
        from app.services.proposal_knowledge_base_tools import normalize_zo_kb_query

        for query in queries:
            text = normalize_zo_kb_query(
                str(query).strip(),
                rfp_client=rfp_client,
                rfp_sector=rfp_sector,
                rfp_title=rfp_title,
            )
            if text and text.lower() not in used:
                cleaned.append(text[:240])
                used.add(text.lower())
        return cleaned[:4]
    except (LlmError, Exception) as exc:
        logger.warning("Query planner agent failed: %s", exc)
        return []


async def redraft_section_agent(
    *,
    role: Literal[AgentRole.SECTION_REPAIR, AgentRole.USER_REVISE, AgentRole.SURGICAL_FIX],
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    user_content: str,
    rfp_sector: str = "",
) -> tuple[dict[str, Any], str, list[str]]:
    """KB tool agent → JSON with content field."""
    return await run_tool_json_agent(
        role=role,
        sector=rfp_sector,
        rfp_id=rfp_id,
        title=rfp_title,
        client=rfp_client,
        user_content=user_content,
    )
