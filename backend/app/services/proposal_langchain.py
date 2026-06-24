import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.services import proposal_knowledge_base_tools
from app.services.llm import LlmError, _fireworks_key, _openrouter_key, chat_json

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4


def get_chat_model() -> ChatOpenAI:
    if _openrouter_key():
        return ChatOpenAI(
            model=settings.openrouter_model,
            api_key=_openrouter_key(),
            base_url=settings.openrouter_base_url.rstrip("/"),
            temperature=0.2,
            max_tokens=4096,
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
        temperature=0.2,
        max_tokens=4096,
    )


def _provider_name() -> str:
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
    tools = build_proposal_tools(rfp_id, title, client)
    tool_map = {t.name: t for t in tools}
    llm = get_chat_model().bind_tools(tools)

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

    messages: list[Any] = [
        SystemMessage(content=system),
        HumanMessage(content=user),
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        response = await llm.ainvoke(messages)
        if not getattr(response, "tool_calls", None):
            messages.append(response)
            break

        messages.append(response)
        for call in response.tool_calls:
            name = call["name"]
            tool = tool_map.get(name)
            if not tool:
                result = f"Unknown tool: {name}"
            else:
                result = await tool.ainvoke(call["args"])
            messages.append(
                ToolMessage(content=str(result)[:12000], tool_call_id=call["id"])
            )
        logger.info(
            "Proposal research agent round %d for %s: %d tool calls",
            round_num + 1,
            rfp_id,
            len(response.tool_calls),
        )

    final = messages[-1]
    content = final.content if hasattr(final, "content") else str(final)
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )

    try:
        parsed = json.loads(str(content))
        answers = parsed.get("answers", [])
        if isinstance(answers, list):
            return answers, _provider_name()
    except json.JSONDecodeError:
        pass

    structured, _ = await chat_json(
        [
            {"role": "system", "content": "Convert the research into JSON answers array only."},
            {
                "role": "user",
                "content": f"Questions: {json.dumps(questions)}\n\nResearch:\n{content[:15000]}",
            },
        ]
    )
    return structured.get("answers", []), _provider_name()
