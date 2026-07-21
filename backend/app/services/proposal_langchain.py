import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.services import proposal_knowledge_base_tools
from app.services.llm import LlmError, LlmTier, _fireworks_key, _openrouter_key, chat_json, resolve_llm_model

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4


def _use_fireworks_primary() -> bool:
    return bool(settings.llm_prefer_fireworks and _fireworks_key())


def get_chat_model(
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    force_fireworks: bool = False,
    tier: LlmTier = "heavy",
) -> ChatOpenAI:
    """LangChain chat model — respects LLM_PREFER_FIREWORKS like chat_json."""
    if force_fireworks or _use_fireworks_primary():
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
    if not _fireworks_key():
        raise LlmError(
            "No LLM API key configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
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
) -> tuple[str, str, list[str]]:
    """Generic LangChain tool-calling loop. Falls back to Fireworks on OpenRouter failure."""
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
            force_fireworks=_use_fireworks_primary(),
            tier=tier,
        )
    except Exception as exc:
        if _fireworks_key() and not _use_fireworks_primary():
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
) -> list[StructuredTool]:
    async def search_knowledge_base(query: str) -> str:
        """Search zö verified knowledge base for company facts, certifications, capabilities."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(query)
        return text

    async def search_master_template(section: str) -> str:
        """Search master template content (02_ prefix) for a proposal section."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            f"zö agency master template 02_ {section} company overview team case study",
            limit=5,
        )
        return text

    async def search_case_studies(sector: str, scope: str) -> str:
        """Search verified case studies (03_CS_) by sector and scope similarity."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            f"03 case study {sector} {scope} zö agency confirmed outcomes",
            limit=6,
        )
        return text

    async def search_team_bios(roles: str) -> str:
        """Search approved team bios (04_Bio_) for required roles."""
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            f"04 bio team {roles} zö agency approved personnel",
            limit=6,
        )
        return text

    async def search_rfp_requirements(topic: str) -> str:
        """Search the ingested RFP document for requirements on a topic."""
        text, _ = await proposal_knowledge_base_tools.search_rfp_document(rfp_id, title, client)
        if topic.strip():
            return f"Topic: {topic}\n\n{text[:8000]}"
        return text[:8000]

    return [
        StructuredTool.from_function(
            coroutine=search_knowledge_base,
            name="search_knowledge_base",
            description="Search zö verified knowledge base.",
        ),
        StructuredTool.from_function(
            coroutine=search_master_template,
            name="search_master_template",
            description="Search master template sections (02_ files).",
        ),
        StructuredTool.from_function(
            coroutine=search_case_studies,
            name="search_case_studies",
            description="Search verified case studies (03_CS_).",
        ),
        StructuredTool.from_function(
            coroutine=search_team_bios,
            name="search_team_bios",
            description="Search approved team bios (04_Bio_).",
        ),
        StructuredTool.from_function(
            coroutine=search_rfp_requirements,
            name="search_rfp_requirements",
            description="Search the source RFP for requirements and evaluation criteria.",
        ),
    ]


async def run_tool_research_agent(
    *,
    rfp_id: str,
    title: str,
    client: str,
    rfp_excerpt: str,
    questions: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], str]:
    system = """You are a proposal research agent for zö agency.
Use the provided tools to answer each research question using ONLY verified knowledge-base and RFP content.
Call tools selectively — batch related questions when possible to save tokens.
When finished, respond with ONLY valid JSON:
{"answers":[{"id":"...","answer":"...","sources":["tool:search_knowledge_base"]}]}
Never invent facts. If not found, say what is missing."""

    user = f"""RFP excerpt:
{rfp_excerpt[:10000]}

Research questions:
{json.dumps(questions, indent=2)}
"""

    tools = build_proposal_tools(rfp_id, title, client)
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
