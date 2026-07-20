"""LangChain agent profiles — one distinct agent per proposal edit loop."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm import LlmError, _fireworks_key, chat_json
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


SENIOR_EDITOR_SYSTEM = """You are zö agency's Senior Proposal Editor.
Review ONE proposal section against its RFP requirements BEFORE the Section Repair agent rewrites it.

Process (use tools — do not skip):
1. Call search_rfp_requirements with the section topic to read source RFP requirements for this section.
2. Call KB tools (search_knowledge_base, search_master_template, search_case_studies, search_team_bios) for facts needed to complete the section.
3. Identify every issue: blank/missing content, [VERIFY] stubs, generation-error text, grammar/wording errors, unmet RFP requirements, wrong voice.

ALWAYS flag these senior-editor priorities when present:
- Subject-verb disagreement after "We were …, and is …" (cover letter / entity description)
- Malformed possessives: "of we", "across we", "sole owner of we" — must use our firm / zö agency / our studio
- Subcontractor inconsistency: Company Background claiming "no subcontractors" while cost proposal lists translation partners — align narrative with budget
- RFP compliance: reference contact names/phones/emails, workforce diversity %, staff hours in budget, PSA acknowledgments (insurance, living wage, MacBride, Title VI) — never defer to unnamed attachments
- Budget: flag agency revenue estimate showing $0 when commission or fees apply — must match commission rate × pass-through
- References: flag "contact on request" or missing phone/email
- Workforce: flag inconsistent % female/minority between MWBE and Personnel sections
- Duplication: flag when this section re-copies Who We Are, full bios, full case studies, FEIN/certs, or brand story that belongs in another section — instruct a concise rewrite with ONE job only

Do NOT rewrite the section yourself — write patch instructions for the Section Repair agent.
Return ONLY JSON when done researching:
{"patchInstructions":"specific steps: what to fetch, what to fix, what RFP reqs to address","priority":"critical|high|medium","issues":["issue 1"],"kbQueries":["queries you ran"]}"""

SECTION_REPAIR_SYSTEM = """You are zö agency's Section Repair agent (self-edit loop after Phase 3).
Your job: search the knowledge base with tools, then produce ONE complete section patch.

Rules:
1. Call KB tools until you have enough facts — do not stop after one search.
2. Remove [VERIFY] stubs when evidence supports real prose. Cite [E#] when using corpus IDs provided.
3. First person we/our in narrative sections — never "The Vendor". Never use "we" as a possessive ("of we", "across we").
4. Use ONLY verified KB and RFP facts. Do not invent clients, contacts, or metrics.
5. Address every RFP requirement listed for this section.
6. SUBMISSION POLISH tasks: fix ONLY the listed defects; preserve all other sentences verbatim.
7. Grammar: "We were established …, and is …" must become "and are …" or be rephrased.
8. Subcontractors: if cost proposal lists translation partners, Company Background must align — zö self-performs marketing/communications; translation partners are scoped separately.
9. RFP compliance: reference contacts with phones and emails, workforce diversity %, budget hours table, PSA acks — from KB only; never defer to unnamed attachments or "upon request".
10. Budget section: agency revenue / commission must be positive dollars matching canonical budget — never $0 when commission model applies.
11. MWBE and Personnel must use the same workforce percentages — align to one HR-verified figure.
12. ANTI-DUPLICATION: This section has ONE job. Do not re-paste company bio, full bios, or full case studies owned by other sections. One short cross-reference is OK — then add NEW detail only. Prefer concise prose.
10. When done researching, respond with ONLY JSON:
{"content":"full section prose","kbRefs":["E1"],"designerNote":null}"""

USER_REVISE_SYSTEM = """You are zö agency's User Revise agent (editor chat / Revise content flow).
The user gave explicit feedback. Search KB with tools for missing facts, then rewrite ONE section.

Rules:
1. Directly address the user's edit request.
2. Call tools for deeper KB search — more specific than the first draft pass.
3. Improve substantially — never return the same [VERIFY] placeholder if tools found support.
4. Preserve zö BRAND VOICE (first person we/our, warm, proof-led).
5. Budget/fee edits: never output $0 agency revenue when commission applies — use rate × pass-through or [VERIFY: Sonja confirm rate].
6. Reference edits: full contact block (name, title, phone, email) — never defer to "on request".
7. Return ONLY JSON: {"content":"...","kbRefs":["E1"],"designerNote":null}"""

SURGICAL_FIX_SYSTEM = """You are zö agency's Surgical Fix agent (pre-submit review auto-fix).
Patch ONE section to clear listed review issues — minimal diff, preserve strong prose.

Rules:
1. Search KB tools only when needed to resolve [VERIFY] or missing facts.
2. Fix wrong-client names, voice issues, and placeholders from the issues list.
3. Do NOT invent facts. Do NOT add marketing fluff to procurement/form sections.
4. Change only what the issues require.
5. Return ONLY JSON: {"content":"full updated section text","kbRefs":[]}"""

QUERY_PLANNER_SYSTEM = """You are zö agency's Query Planner agent.
Plan 3-4 NEW Supermemory search queries for ONE proposal section.
Prior queries failed or were insufficient. Never repeat prior queries.
Use hints: 02 master template, 03_CS case studies, 04 bio, certifications, org chart, references.
Return ONLY JSON: {"queries":["query 1","query 2","query 3"]}"""

AGENT_PROFILES: dict[AgentRole, AgentProfile] = {
    AgentRole.SENIOR_EDITOR: AgentProfile(
        role=AgentRole.SENIOR_EDITOR,
        label="Senior Proposal Editor",
        temperature=0.15,
        max_tokens=1024,
        max_tool_rounds=3,
        system_prompt=SENIOR_EDITOR_SYSTEM,
    ),
    AgentRole.SECTION_REPAIR: AgentProfile(
        role=AgentRole.SECTION_REPAIR,
        label="Section Repair",
        temperature=0.3,
        max_tokens=4096,
        max_tool_rounds=2,
        system_prompt=SECTION_REPAIR_SYSTEM,
    ),
    AgentRole.USER_REVISE: AgentProfile(
        role=AgentRole.USER_REVISE,
        label="User Revise",
        temperature=0.35,
        max_tokens=4096,
        max_tool_rounds=4,
        system_prompt=USER_REVISE_SYSTEM,
    ),
    AgentRole.SURGICAL_FIX: AgentProfile(
        role=AgentRole.SURGICAL_FIX,
        label="Surgical Fix",
        temperature=0.15,
        max_tokens=4096,
        max_tool_rounds=3,
        system_prompt=SURGICAL_FIX_SYSTEM,
    ),
    AgentRole.QUERY_PLANNER: AgentProfile(
        role=AgentRole.QUERY_PLANNER,
        label="Query Planner",
        temperature=0.35,
        max_tokens=1024,
        max_tool_rounds=0,
        system_prompt=QUERY_PLANNER_SYSTEM,
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
    for key in ("content", "sectionContent", "section_content", "text", "prose"):
        val = parsed.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    salvaged = _salvage_content_string(raw_text)
    if salvaged:
        return salvaged
    stripped = raw_text.strip()
    if stripped and not stripped.startswith("{"):
        return stripped
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
    force_fireworks = _use_fireworks_primary()

    async def _invoke(*, fireworks: bool) -> dict[str, Any]:
        llm = get_chat_model(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            force_fireworks=fireworks,
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
) -> tuple[dict[str, Any], str, list[str]]:
    """Multi-turn LangChain agent with KB tools — repair, revise, surgical fix."""
    profile = get_profile(role)
    tools = build_proposal_tools(rfp_id, title, client)
    final_text, provider, tool_log = await run_tool_agent_loop(
        system_prompt=profile.system_prompt,
        user_content=user_content,
        tools=tools,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        max_rounds=profile.max_tool_rounds,
        agent_label=profile.label,
        rfp_id=rfp_id,
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
    """Senior editor: read RFP + KB via tools, return patch instructions for Section Repair."""
    profile = get_profile(AgentRole.SENIOR_EDITOR)
    tools = build_proposal_tools(rfp_id, rfp_title, rfp_client)
    req_block = "\n".join(f"- {r}" for r in (requirements or [])) or "(use search_rfp_requirements)"
    user_content = (
        f"Client: {rfp_client}\nRFP: {rfp_title}\n"
        f"Section: {section_title}\nWord target: {word_target}\n"
        f"Mapped RFP requirements:\n{req_block}\n\n"
        f"Current draft:\n{section_content[:5000]}"
    )
    try:
        final_text, _provider, _tool_log = await run_tool_agent_loop(
            system_prompt=profile.system_prompt,
            user_content=user_content,
            tools=tools,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            max_rounds=profile.max_tool_rounds,
            agent_label=profile.label,
            rfp_id=rfp_id,
        )
        raw = await _parse_json_from_agent_text(final_text)
        instructions = str(raw.get("patchInstructions") or "").strip()
        issues = raw.get("issues") or []
        if isinstance(issues, list) and issues:
            issue_lines = "\n".join(f"- {i}" for i in issues[:6] if str(i).strip())
            if issue_lines:
                instructions = f"{instructions}\n\nIssues flagged:\n{issue_lines}".strip()
        if instructions:
            return instructions
    except (LlmError, Exception) as exc:
        logger.warning("Senior editor agent failed for %s: %s", section_title, exc)
    return ""


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
) -> list[str]:
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
        for query in queries:
            text = str(query).strip()
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
) -> tuple[dict[str, Any], str, list[str]]:
    """KB tool agent → JSON with content field."""
    return await run_tool_json_agent(
        role=role,
        rfp_id=rfp_id,
        title=rfp_title,
        client=rfp_client,
        user_content=user_content,
    )
