"""Case Study Builder — retrieve full doc per selected study and write concise case study."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import ProposalContext
from app.services.llm import LlmError
from app.services.proposal_integrity_guards import (
    case_study_fidelity_ok,
    case_study_has_required_structure,
    case_study_looks_like_source_dump,
    prefer_case_study_kb_text,
    scrub_case_study_overbuild,
)
from app.services.proposal_manuscript_locks import strip_internal_proposal_meta

logger = logging.getLogger(__name__)


def _case_study_stub(study_title: str, reason: str) -> str:
    return (
        f"### {study_title}\n\n"
        f"**Challenge**\n\n"
        f"[VERIFY: rewrite Challenge from source case study — {reason}]\n\n"
        f"**Solution / Our Approach**\n\n"
        f"[VERIFY: rewrite Solution from source case study — {reason}]\n\n"
        f"Client Voice: [VERIFY: no client quote found in source material]"
    )


def _builder_system_prompt(*, study_title: str, rfp_client: str, strict: bool) -> str:
    strict_extra = ""
    if strict:
        strict_extra = (
            "\nRETRY MODE — previous output was REJECTED as a source dump.\n"
            "- Do NOT copy filenames, TOC, cover letters, [photo] OCR, or proposal boilerplate.\n"
            "- Do NOT paste 03_CS_ / 06_WON_ labels into the prose.\n"
            "- Extract only the engagement facts and rewrite into the three headings below.\n"
            "- If the source is mostly a full proposal with little case-study narrative, still "
            "write Challenge + Solution from whatever project facts exist, then Client Voice VERIFY.\n"
        )
    return (
        "You are the Case Study Builder for zö agency Section 3.\n"
        f"Write a concise case study for: '{study_title}'.\n\n"
        f"{strict_extra}"
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
    )


async def _invoke_case_study_llm(
    *,
    study_title: str,
    case_study_text: str,
    proposal_context: ProposalContext,
    rfp_client: str,
    brand_voice_block: str,
    kb_sources: list[str],
    prior_sections_digest: str,
    strict: bool,
) -> tuple[dict[str, str | list[str]], str]:
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": _builder_system_prompt(
                    study_title=study_title,
                    rfp_client=rfp_client,
                    strict=strict,
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
    return raw, provider


def _postprocess_case_study_content(
    *,
    study_title: str,
    content: str,
    case_study_text: str,
) -> tuple[str, str | None]:
    """
    Scrub + validate. Returns (content, reject_reason).

    reject_reason is set when the card must be retried or stubbed.
    """
    content = strip_internal_proposal_meta(content or "")
    if not content.strip():
        return _case_study_stub(study_title, "empty model output"), "empty"

    content, scrub_logs = scrub_case_study_overbuild(content)
    if scrub_logs:
        logger.info(
            "Case study overbuild scrub for %s: %s",
            study_title,
            "; ".join(scrub_logs)[:240],
        )

    dump, dump_reason = case_study_looks_like_source_dump(content)
    if dump:
        return content, f"source dump ({dump_reason})"

    if not case_study_has_required_structure(content):
        return content, "missing Challenge/Solution structure"

    ok, reason = case_study_fidelity_ok(case_study_text, content)
    if not ok:
        return content, f"fidelity failed ({reason})"

    return content, None


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
    filtered_text, cs_labels = prefer_case_study_kb_text(case_study_text)
    if cs_labels:
        logger.info(
            "Case study builder preferring 03_CS sources for %s: %s",
            study_title,
            cs_labels[:4],
        )
        # Keep metadata honest: prefer filtered labels when available.
        kb_sources = list(dict.fromkeys([*cs_labels, *kb_sources]))
    source_for_llm = filtered_text or case_study_text

    provider = "failed"
    raw: dict[str, str | list[str]] = {}
    content = ""
    reject_reason: str | None = None

    for attempt, strict in ((1, False), (2, True)):
        try:
            raw, provider = await _invoke_case_study_llm(
                study_title=study_title,
                case_study_text=source_for_llm,
                proposal_context=proposal_context,
                rfp_client=rfp_client,
                brand_voice_block=brand_voice_block,
                kb_sources=kb_sources,
                prior_sections_digest=prior_sections_digest,
                strict=strict,
            )
        except LlmError as exc:
            logger.warning(
                "Case study builder failed for %s attempt=%s (%s)",
                study_title,
                attempt,
                str(exc)[:160],
            )
            if attempt == 1:
                continue
            stub = _case_study_stub(study_title, "generation interrupted")
            return {"content": stub, "kbRefs": list(kb_sources)}, "failed"

        content = str(raw.get("content") or "").strip()
        content, reject_reason = _postprocess_case_study_content(
            study_title=study_title,
            content=content,
            case_study_text=source_for_llm,
        )
        if reject_reason is None:
            break
        logger.warning(
            "Case study builder rejected output for %s attempt=%s: %s",
            study_title,
            attempt,
            reject_reason,
        )

    if reject_reason is not None:
        # Source dumps / fidelity failures must not ship. Missing headings alone can
        # still be usable prose (retry already attempted); keep rather than blanking.
        if reject_reason.startswith("source dump") or reject_reason.startswith(
            "fidelity failed"
        ) or reject_reason == "empty":
            content = _case_study_stub(study_title, reject_reason)
            logger.warning(
                "Case study builder stubbing %s after retries: %s",
                study_title,
                reject_reason,
            )
        else:
            logger.warning(
                "Case study builder keeping non-dump output for %s after retries: %s",
                study_title,
                reject_reason,
            )

    refs = raw.get("kbRefs") or raw.get("kb_refs") or kb_sources
    if not isinstance(refs, list):
        refs = kb_sources
    return {"content": content, "kbRefs": [str(r) for r in refs if str(r).strip()]}, provider
