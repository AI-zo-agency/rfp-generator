"""Case Study Builder — retrieve full doc per selected study and write concise case study."""

from __future__ import annotations

import logging
import re

from app.services import llm
from app.services.company_qualification.schemas import ProposalContext
from app.services.llm import LlmError

logger = logging.getLogger(__name__)


async def run_case_study_builder_agent(
    *,
    study_title: str,
    case_study_text: str,
    proposal_context: ProposalContext,
    rfp_client: str,
    brand_voice_block: str,
    kb_sources: list[str],
    prior_sections_digest: str = "",
) -> tuple[dict[str, str | list[str]], str]:
    try:
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
                        "- Keep the REAL project name and what the engagement actually was "
                        "(festival, ticket sales, VIP, launch window, etc.). "
                        "NEVER genericize into a vague 'municipal communications / community "
                        "outreach' story the source does not support — that is fabrication.\n"
                        "- Why Relevant may connect to THIS RFP; Challenge / Solution / Results "
                        "must stay faithful to the source document.\n"
                        "- If facts are missing, use [VERIFY] — do NOT invent.\n"
                        "- Do NOT include Source:, filename, .pdf, .docx, or knowledge-base citations "
                        "in the client-facing prose. Sources stay in metadata only.\n"
                        "- NEVER write meta notes like 'the requested file was not present', "
                        "'Case Study Master', 'pull additional metrics before submission', "
                        "'Creative Examples:', or any word-count labels — those are internal only.\n"
                        "- End after Why Relevant. Do not append catalogs of creative examples.\n"
                        "- This case study appears ONCE in the proposal (Section 3 only). "
                        "Do not repeat company overview, team roster, or other case studies.\n"
                        "- Return ONE complete JSON object — no markdown fences.\n\n"
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
                        + (
                            f"{prior_sections_digest}\n\n"
                            if prior_sections_digest.strip()
                            else ""
                        )
                        + f"proposalType: {proposal_context.proposal_type}\n"
                        f"industry: {proposal_context.industry}\n\n"
                        f"Retrieved case study document:\n{case_study_text[:60000]}\n\n"
                        f"Known sources: {kb_sources}"
                    ),
                },
            ],
            max_tokens=3072,
            temperature=0.0,
            tier="heavy",
        )
    except LlmError as exc:
        logger.warning(
            "Case study builder failed for %s (%s); VERIFY stub (no retry)",
            study_title,
            str(exc)[:160],
        )
        stub = (
            f"### {study_title}\n\n"
            f"[VERIFY: rewrite case study from source document — generation interrupted]\n\n"
            f"Why Relevant: Supports {proposal_context.proposal_type or 'this RFP'} scope."
        )
        return {"content": stub, "kbRefs": list(kb_sources)}, "failed"

    content = str(raw.get("content") or "").strip()
    from app.services.proposal_manuscript_locks import strip_internal_proposal_meta

    content = strip_internal_proposal_meta(content)
    if not content:
        content = (
            f"### {study_title}\n\n"
            f"[VERIFY: case study content missing after parse — complete from KB]"
        )
    else:
        from app.services.proposal_integrity_guards import case_study_fidelity_ok

        ok, reason = case_study_fidelity_ok(case_study_text, content)
        if not ok:
            logger.warning(
                "Case study fidelity failed for %s: %s — forcing source-faithful stub",
                study_title,
                reason,
            )
            # Prefer a short faithful extract over a genericized rewrite.
            snippet = re.sub(r"\s+", " ", (case_study_text or "")[:1800]).strip()
            content = (
                f"### {study_title}\n\n"
                f"{snippet}\n\n"
                f"Why Relevant: Supports {proposal_context.proposal_type or 'this RFP'} "
                f"scope with documented outcomes from the verified engagement above.\n\n"
                f"[VERIFY: polish Why Relevant only — do not rewrite Challenge/Solution/Results "
                f"away from the source file]"
            )
    refs = raw.get("kbRefs") or raw.get("kb_refs") or kb_sources
    if not isinstance(refs, list):
        refs = kb_sources
    return {"content": content, "kbRefs": [str(r) for r in refs if str(r).strip()]}, provider
