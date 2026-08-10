import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.services import proposal_knowledge_base_tools
from app.services.llm import LlmError, LlmTier, _fireworks_key, _openrouter_key, chat_json, resolve_llm_model
from app.services.llm_routing import is_quality_critical_node

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4


def _use_fireworks_primary() -> bool:
    if settings.llm_disable_fireworks:
        return False
    return bool(settings.llm_prefer_fireworks and _fireworks_key())


def _prefer_fireworks_for_node(node_name: str | None) -> bool:
    """Honour LLM_PREFER_FIREWORKS except for quality-critical judgment nodes.

    This layer (Senior Editor, Section Repair, User Revise, Surgical Fix) does
    not go through llm.chat_json, so it needs the same stage-map check. When a
    quality node has no alternative provider configured we still serve it from
    Fireworks — but we say so, because a silent downgrade here means the agent
    judging the proposal is weaker than the one that wrote it.
    """
    if settings.llm_disable_fireworks:
        return False
    if not _use_fireworks_primary():
        return False
    if not is_quality_critical_node(node_name):
        return True
    if _openrouter_key() and settings.llm_openrouter_enabled:
        logger.info(
            "llm_stage_map quality_node=%s routed off Fireworks to OpenRouter",
            node_name or "unnamed",
        )
        return False
    logger.warning(
        "llm_stage_map quality_node=%s served by economy model — OpenRouter "
        "unavailable (key=%s, enabled=%s). Judgment quality is degraded for "
        "this stage; set LLM_OPENROUTER_ENABLED=true once credits are funded.",
        node_name or "unnamed",
        bool(_openrouter_key()),
        settings.llm_openrouter_enabled,
    )
    return True


def get_chat_model(
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    force_fireworks: bool = False,
    tier: LlmTier = "heavy",
    node_name: str | None = None,
) -> ChatOpenAI:
    """LangChain chat model — respects LLM_PREFER_FIREWORKS like chat_json."""
    if settings.llm_disable_fireworks and force_fireworks:
        raise LlmError(
            "Fireworks is disabled (LLM_DISABLE_FIREWORKS). Use OpenRouter or Gemini.",
            status_code=503,
        )
    if (force_fireworks or _prefer_fireworks_for_node(node_name)) and not settings.llm_disable_fireworks:
        if not _fireworks_key():
            raise LlmError(
                "FIREWORKS_API_KEY required when LLM_PREFER_FIREWORKS is set.",
                status_code=503,
            )
        return ChatOpenAI(
            model=settings.fireworks_model,
            api_key=_fireworks_key(),
            base_url=settings.fireworks_base_url.rstrip("/"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if _openrouter_key():
        return ChatOpenAI(
            model=resolve_llm_model(tier),
            api_key=_openrouter_key(),
            base_url=settings.openrouter_base_url.rstrip("/"),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if settings.llm_disable_fireworks or not _fireworks_key():
        raise LlmError(
            "No LLM API key configured. Set OPENROUTER_API_KEY"
            + ("." if settings.llm_disable_fireworks else " or FIREWORKS_API_KEY."),
            status_code=503,
        )
    return ChatOpenAI(
        model=settings.fireworks_model,
        api_key=_fireworks_key(),
        base_url=settings.fireworks_base_url.rstrip("/"),
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def run_tool_agent_loop(
    *,
    system_prompt: str,
    user_content: str,
    tools: list[StructuredTool],
    temperature: float,
    max_tokens: int,
    max_rounds: int,
    agent_label: str,
    rfp_id: str = "",
    tier: LlmTier = "heavy",
    node_name: str | None = None,
) -> tuple[str, str, list[str]]:
    """Generic LangChain tool-calling loop. Falls back to Fireworks on OpenRouter failure."""
    used_fireworks_first = _prefer_fireworks_for_node(node_name)
    try:
        return await _run_tool_agent_loop_once(
            system_prompt=system_prompt,
            user_content=user_content,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            max_rounds=max_rounds,
            agent_label=agent_label,
            rfp_id=rfp_id,
            force_fireworks=used_fireworks_first,
            tier=tier,
        )
    except Exception as exc:
        # Only worth retrying on Fireworks if the first attempt was not already there.
        if (
            not settings.llm_disable_fireworks
            and _fireworks_key()
            and not used_fireworks_first
        ):
            logger.warning(
                "%s agent primary LLM failed (%s) — retrying via Fireworks",
                agent_label,
                exc,
            )
            return await _run_tool_agent_loop_once(
                system_prompt=system_prompt,
                user_content=user_content,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                max_rounds=max_rounds,
                agent_label=agent_label,
                rfp_id=rfp_id,
                force_fireworks=True,
                tier=tier,
            )
        raise


_SYNTHESIS_NUDGE = (
    "KB search rounds are complete. Write the full section now using the evidence above. "
    'Return ONLY valid JSON with a non-empty "content" field — no more tool calls.'
)


def _message_text(message: Any) -> str:
    content = message.content if hasattr(message, "content") else str(message)
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


async def _run_tool_agent_loop_once(
    *,
    system_prompt: str,
    user_content: str,
    tools: list[StructuredTool],
    temperature: float,
    max_tokens: int,
    max_rounds: int,
    agent_label: str,
    rfp_id: str,
    force_fireworks: bool,
    tier: LlmTier = "heavy",
) -> tuple[str, str, list[str]]:
    """Single provider attempt for tool-calling loop."""
    tool_map = {t.name: t for t in tools}
    base_llm = get_chat_model(
        temperature=temperature,
        max_tokens=max_tokens,
        force_fireworks=force_fireworks,
        tier=tier,
    )
    tool_llm = base_llm.bind_tools(tools)
    messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    tool_log: list[str] = []

    for round_num in range(max_rounds):
        response = await tool_llm.ainvoke(messages)
        if not getattr(response, "tool_calls", None):
            messages.append(response)
            break

        messages.append(response)
        for call in response.tool_calls:
            name = call["name"]
            tool_log.append(name)
            tool = tool_map.get(name)
            if not tool:
                result = f"Unknown tool: {name}"
            else:
                result = await tool.ainvoke(call["args"])
            messages.append(
                ToolMessage(content=str(result)[:12000], tool_call_id=call["id"])
            )
        logger.info(
            "%s agent round %d for %s: tools=%s provider=%s",
            agent_label,
            round_num + 1,
            rfp_id or "n/a",
            response.tool_calls,
            _provider_name(force_fireworks=force_fireworks),
        )

        if round_num == max_rounds - 1:
            messages.append(HumanMessage(content=_SYNTHESIS_NUDGE))
            synthesis = await base_llm.ainvoke(messages)
            messages.append(synthesis)
            logger.info(
                "%s agent synthesis for %s after %d tool round(s) provider=%s",
                agent_label,
                rfp_id or "n/a",
                max_rounds,
                _provider_name(force_fireworks=force_fireworks),
            )

    final = messages[-1]
    if getattr(final, "tool_calls", None):
        logger.warning(
            "%s agent for %s ended on tool_calls without synthesis — forcing JSON turn",
            agent_label,
            rfp_id or "n/a",
        )
        messages.append(HumanMessage(content=_SYNTHESIS_NUDGE))
        synthesis = await base_llm.ainvoke(messages)
        messages.append(synthesis)
        final = synthesis

    return (
        _message_text(final),
        _provider_name(force_fireworks=force_fireworks),
        tool_log,
    )


def _provider_name(*, force_fireworks: bool = False) -> str:
    if force_fireworks or _use_fireworks_primary():
        return "fireworks"
    return "openrouter" if _openrouter_key() else "fireworks"


def build_proposal_tools(
    rfp_id: str,
    title: str,
    client: str,
    sector: str = "",
) -> list[StructuredTool]:
    async def search_knowledge_base(query: str) -> str:
        """Search zö verified KB for company facts, bios, case studies, certifications.

        KB is ONLY about zö — never query with the RFP buyer as the subject.
        Ask what zö can provide (capabilities, case studies, bios) matching RFP themes.
        For buyer requirements use search_rfp_requirements. For budget use
        search_rfp_requirements + search_pricing_guide.
        """
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            query,
            limit=5,
            max_chars=12_000,
            rfp_client=client,
            rfp_sector=sector,
            rfp_title=title,
        )
        return text

    async def search_master_template(section: str) -> str:
        """Search master template content (02_ prefix) for a proposal section."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            f"zö agency master template 02_ {section} company overview team case study",
            limit=4,
            max_chars=12_000,
            rfp_client=client,
            rfp_sector=sector,
            rfp_title=title,
        )
        return text

    async def search_case_studies(sector_hint: str, scope: str) -> str:
        """Search verified case studies (03_CS_) by sector and scope similarity."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            f"03 case study {sector_hint or sector} {scope} zö agency confirmed outcomes",
            limit=4,
            max_chars=12_000,
            rfp_client=client,
            rfp_sector=sector,
            rfp_title=title,
        )
        return text

    async def search_team_bios(roles: str) -> str:
        """Search approved team bios (04_Bio_) for required roles."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            f"04 bio team {roles} zö agency approved personnel",
            limit=4,
            max_chars=12_000,
            rfp_client=client,
            rfp_sector=sector,
            rfp_title=title,
        )
        return text

    async def search_rfp_requirements(topic: str) -> str:
        """Search THIS RFP for buyer requirements (scope, scoring, forms, references format)."""
        text, _ = await proposal_knowledge_base_tools.search_rfp_document(rfp_id, title, client)
        if topic.strip():
            return f"Topic: {topic}\n\n{text[:8000]}"
        return text[:8000]

    async def search_pricing_guide(topic: str = "") -> str:
        """Search ONLY 00_Guide_Pricing (Low/Average/High tiers, menu rates, PM floor).

        Pass pricing vocabulary only (tiers, rates, PM floor). NEVER pass the RFP
        client name or RFP title — the guide has no client-specific prices.
        """
        hint = proposal_knowledge_base_tools.sanitize_pricing_guide_query(
            topic or "tier ranges Low Average High discovery strategy fees",
            rfp_client=client,
            rfp_title=title,
        )
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            hint,
            limit=8,
            category="pricing",
            max_chars=12_000,
            rfp_client=client,
            rfp_sector=sector,
            rfp_title=title,
        )
        if text and not text.startswith("("):
            return text
        text2, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            hint,
            limit=8,
            category="reference",
            max_chars=12_000,
            rfp_client=client,
            rfp_sector=sector,
            rfp_title=title,
        )
        return text2 or "(No 00_Guide_Pricing content found.)"

    return [
        StructuredTool.from_function(
            coroutine=search_knowledge_base,
            name="search_knowledge_base",
            description=(
                "Search zö KB ONLY (company facts, bios, case studies). "
                "Query what zö can provide for the RFP theme — NEVER search for the "
                "RFP buyer/prospect by name (they are not in the KB). "
                "Buyer requirements → search_rfp_requirements. "
                "Budget → search_rfp_requirements + search_pricing_guide."
            ),
        ),
        StructuredTool.from_function(
            coroutine=search_master_template,
            name="search_master_template",
            description="Search master template sections (02_ files).",
        ),
        StructuredTool.from_function(
            coroutine=search_case_studies,
            name="search_case_studies",
            description=(
                "Search verified zö case studies (03_CS_) by sector/scope theme — "
                "not by RFP buyer name."
            ),
        ),
        StructuredTool.from_function(
            coroutine=search_team_bios,
            name="search_team_bios",
            description="Search approved team bios (04_Bio_).",
        ),
        StructuredTool.from_function(
            coroutine=search_rfp_requirements,
            name="search_rfp_requirements",
            description=(
                "Search THIS RFP PDF for buyer requirements — scope, scoring, fee forms, "
                "reference format, past-performance rules. Use this for anything about "
                "what the buyer demands (not zö facts)."
            ),
        ),
        StructuredTool.from_function(
            coroutine=search_pricing_guide,
            name="search_pricing_guide",
            description=(
                "Search 00_Guide_Pricing only — Low/Average/High tiers and approved rate menu. "
                "Args: pricing terms ONLY (e.g. 'Average tier PM floor discovery'). "
                "NEVER include RFP client name or RFP title."
            ),
        ),
    ]


async def run_tool_research_agent(
    *,
    rfp_id: str,
    title: str,
    client: str,
    rfp_excerpt: str,
    questions: list[dict[str, str]],
    sector: str = "",
) -> tuple[list[dict[str, Any]], str]:
    system = """You are a proposal research agent for zö agency.
Use the provided tools to answer each research question using ONLY verified knowledge-base and RFP content.
KB tools = zö facts only. Never search KB with the RFP buyer as the subject — use search_rfp_requirements for buyer demands.
Call tools selectively — batch related questions when possible to save tokens.
When finished, respond with ONLY valid JSON:
{"answers":[{"id":"...","answer":"...","sources":["tool:search_knowledge_base"]}]}
Never invent facts. If not found, say what is missing."""

    user = f"""RFP excerpt:
{rfp_excerpt[:10000]}

Research questions:
{json.dumps(questions, indent=2)}
"""

    tools = build_proposal_tools(rfp_id, title, client, sector=sector)
    final_text, provider, _tool_log = await run_tool_agent_loop(
        system_prompt=system,
        user_content=user,
        tools=tools,
        temperature=0.2,
        max_tokens=4096,
        max_rounds=MAX_TOOL_ROUNDS,
        agent_label="Research",
        rfp_id=rfp_id,
    )

    try:
        parsed = json.loads(final_text)
        answers = parsed.get("answers", [])
        if isinstance(answers, list):
            return answers, provider
    except json.JSONDecodeError:
        pass

    structured, _ = await chat_json(
        [
            {"role": "system", "content": "Convert the research into JSON answers array only."},
            {
                "role": "user",
                "content": f"Questions: {json.dumps(questions)}\n\nResearch:\n{final_text[:15000]}",
            },
        ]
    )
    return structured.get("answers", []), provider
