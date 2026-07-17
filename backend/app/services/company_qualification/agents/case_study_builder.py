"""Case Study Builder — retrieve full doc per selected study and write concise case study."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import ProposalContext

logger = logging.getLogger(__name__)


async def run_case_study_builder_agent(
    *,
    study_title: str,
    case_study_text: str,
    proposal_context: ProposalContext,
    rfp_client: str,
    brand_voice_block: str,
    kb_sources: list[str],
) -> tuple[dict[str, str | list[str]], str]:
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Case Study Builder for zö agency Section 3.\n"
                    f"Write a concise case study for: '{study_title}'.\n\n"
                    "CRITICAL RULES:\n"
                    f"- Do NOT write about '{rfp_client}' — that is the CURRENT client.\n"
                    "- ONLY use verified facts from the retrieved case study document below.\n"
                    "- If facts are missing, use [VERIFY] — do NOT invent.\n"
                    "- Do NOT include Source:, filename, .pdf, .docx, or knowledge-base citations "
                    "in the client-facing prose. Sources stay in metadata only.\n\n"
                    "Template:\n"
                    "- Client overview\n"
                    "- Challenge\n"
                    "- Solution / Our Approach\n"
                    "- Results (bold key metrics)\n"
                    "- Why Relevant (1-2 sentences tied to RFP context)\n\n"
                    "Keep concise — one page max. ASCII only.\n"
                    'Return JSON: {"content": "markdown case study", "kbRefs": ["source file names"]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Voice:\n{brand_voice_block}\n\n"
                    f"Proposal context:\n{proposal_context.model_dump_json()}\n\n"
                    f"Retrieved case study document:\n{case_study_text[:120000]}\n\n"
                    f"Known sources: {kb_sources}"
                ),
            },
        ],
        max_tokens=2048,
        temperature=0.0,
    )

    content = str(raw.get("content") or "").strip()
    refs = raw.get("kbRefs") or raw.get("kb_refs") or kb_sources
    if not isinstance(refs, list):
        refs = kb_sources
    return {"content": content, "kbRefs": [str(r) for r in refs if str(r).strip()]}, provider
