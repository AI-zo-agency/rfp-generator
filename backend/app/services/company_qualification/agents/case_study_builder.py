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
                        "- Challenge, Solution, and Client Voice must all stay faithful to the "
                        "source document.\n"
                        "- If facts are missing, use [VERIFY] — do NOT invent.\n"
                        "- Do NOT include Source:, filename, .pdf, .docx, or knowledge-base citations "
                        "in the client-facing prose. Sources stay in metadata only.\n"
                        "- NEVER write meta notes like 'the requested file was not present', "
                        "'Case Study Master', 'pull additional metrics before submission', "
                        "'Creative Examples:', or any word-count labels — those are internal only.\n"
                        "- End after Client Voice (or its [VERIFY] line if no quote exists). "
                        "Do not append catalogs of creative examples, KPI lists, or metrics.\n"
                        "- This case study appears ONCE in the proposal (Section 3 only). "
                        "Do not repeat company overview, team roster, or other case studies.\n"
                        "- Return ONE complete JSON object — no markdown fences.\n\n"
                        "Template — exactly these three sections, nothing else:\n"
                        "- Challenge (the problem or situation the client faced)\n"
                        "- Solution / Our Approach\n"
                        "- Client Voice — a short client quote copied VERBATIM from the source "
                        "document, in quotation marks, with the speaker's name/title if the "
                        "source gives one. Do not paraphrase, shorten into your own words, "
                        "embellish, or invent a quote. If the source document contains no "
                        "client quote, write exactly this line and nothing else for that "
                        "section: [VERIFY: no client quote found in source material]\n\n"
                        "Do NOT include a company/client overview, a results or KPI/metrics "
                        "list, or a 'Why Relevant' section — Challenge, Solution, and Client "
                        "Voice only.\n\n"
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
            f"Client Voice: [VERIFY: no client quote found in source material]"
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
                f"Client Voice: [VERIFY: no client quote found in source material]\n\n"
                f"[VERIFY: polish Challenge/Solution wording only — do not rewrite the facts "
                f"away from the source file]"
            )
    refs = raw.get("kbRefs") or raw.get("kb_refs") or kb_sources
    if not isinstance(refs, list):
        refs = kb_sources
    return {"content": content, "kbRefs": [str(r) for r in refs if str(r).strip()]}, provider
