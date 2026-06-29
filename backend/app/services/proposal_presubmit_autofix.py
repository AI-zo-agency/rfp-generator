"""Pre-submit auto-fix — deterministic cleanup then AI + Supermemory repair per section."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import (
    EvidenceItem,
    PreSubmitIssue,
    PreSubmitReview,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
    SectionAutoFixLog,
)
from app.models.rfp import RfpRecord
from app.services import llm, proposal_knowledge_base_tools, supermemory
from app.services.llm import LlmError
from app.services.proposal_brand_voice import (
    classify_section_register,
    format_brand_voice_block,
    resolve_voice_context,
)
from app.services.proposal_loss_lessons import format_avoidance_block
from app.services.proposal_presubmit_review import fix_stale_client_references, run_presubmit_review
from app.services.proposal_repository import save_proposal_draft
from app.services.proposal_retrieval_graph import (
    EXCERPT_MAX_CHARS,
    SEARCH_LIMIT,
    _hit_excerpt,
    _hit_key,
    _hit_label,
)
from app.services.proposal_voice_enforcement import enforce_narrative_voice

logger = logging.getLogger(__name__)

STATIC_SECTION_IDS = (
    "section-1-company-overview",
    "section-2-team-overview",
    "section-3-our-work",
)

MAX_ITERATIONS_DETERMINISTIC = 1
MAX_ITERATIONS_LLM = 2
_AUTO_FIX_CATEGORIES = frozenset({"copy_paste", "voice", "placeholder"})

_SEV_RANK = {"critical": 0, "warning": 1, "info": 2}
_CAT_RANK = {"placeholder": 0, "copy_paste": 1, "voice": 2, "compliance": 3}

SURGICAL_FIX_PROMPT = """You repair ONE proposal section to resolve ALL listed pre-submit review issues.

MANDATORY:
1. Preserve section structure — headings, lists, paragraph order, approximate length, and any [DESIGNER NOTE: ...] blocks.
2. Preserve zö BRAND VOICE from the voice block — first person we/our in narrative sections.
3. Use ONLY facts from the evidence corpus / KB excerpts. Cite inline as [E1], [E2], etc. when using evidence.
4. Replace [VERIFY: ...] tags with real prose FROM evidence when available; remove resolved tags entirely.
5. Keep a short [VERIFY: ...] ONLY for requirements still missing from evidence after search.
6. Wrong-client names → use the target client name or remove the stray reference.
7. Voice issues → never "The Vendor", "The Offeror", or third-person agency distance in narrative prose.
8. Do NOT invent clients, metrics, certifications, team members, or dates not supported by evidence.
9. Keep strong existing prose — change only what is needed to clear the listed issues.

Return ONLY JSON: {"content": "full updated section text", "kbRefs": ["E1"]}"""

_search_semaphore = asyncio.Semaphore(4)

CancelCheck = Callable[[], Awaitable[bool]]


def _issue_fingerprint(review: PreSubmitReview) -> str:
    parts = sorted(
        f"{i.section_id}:{i.category}:{i.severity}:{i.message}"
        for i in review.issues
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _issue_sort_key(issue: PreSubmitIssue) -> tuple[int, int, str]:
    return (
        _SEV_RANK.get(issue.severity, 9),
        _CAT_RANK.get(issue.category, 9),
        issue.section_id or "",
    )


def _group_issues_by_section(
    issues: list[PreSubmitIssue],
) -> dict[str, list[PreSubmitIssue]]:
    grouped: dict[str, list[PreSubmitIssue]] = {}
    for issue in issues:
        if not issue.section_id:
            continue
        if issue.category not in _AUTO_FIX_CATEGORIES:
            continue
        grouped.setdefault(issue.section_id, []).append(issue)
    for section_id in grouped:
        grouped[section_id].sort(key=_issue_sort_key)
    return grouped


def _find_rfp_section(
    research: ProposalResearchCache | None,
    section_id: str,
) -> RfpSectionMap | None:
    if not research:
        return None
    for section in research.rfp_sections:
        if section.id == section_id:
            return section
    return None


def _apply_deterministic_fixes(
    section: ProposalSection,
    rfp: RfpRecord,
) -> tuple[str, list[str]]:
    methods: list[str] = []
    content = section.content

    fixed, stale_count = fix_stale_client_references(content, rfp)
    if stale_count > 0 and fixed != content:
        content = fixed
        methods.append(f"stale_client×{stale_count}")

    voiced = enforce_narrative_voice(
        content,
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    if voiced != content:
        content = voiced
        methods.append("voice_register")

    return content, methods


def _issues_summary(issues: list[PreSubmitIssue]) -> str:
    lines = []
    for issue in issues[:16]:
        lines.append(f"- [{issue.severity}/{issue.category}] {issue.message}")
    if len(issues) > 16:
        lines.append(f"- ... and {len(issues) - 16} more")
    return "\n".join(lines)


def _extract_verify_hints(content: str) -> list[str]:
    return [m.strip()[:120] for m in re.findall(r"\[VERIFY:\s*([^\]]+)\]", content, re.I)[:4]]


def _base_pass_queries(rfp: RfpRecord) -> list[str]:
    """Generic KB queries — run once per pass, not per section."""
    return [
        f"zö agency 02 master template certifications org chart employee count {rfp.sector}"[:240],
        f"zö agency 03 case studies {rfp.sector} outcomes metrics"[:240],
        f"zö agency 04 bio team w9 tax id duns contact information"[:240],
    ]


def _section_specific_queries(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    issues: list[PreSubmitIssue],
    rfp_section: RfpSectionMap | None,
) -> list[str]:
    cats = {i.category for i in issues}
    queries: list[str] = []

    if "placeholder" in cats:
        for hint in _extract_verify_hints(section.content)[:3]:
            queries.append(f"zö agency {hint} {rfp.sector}"[:240])

    if "copy_paste" in cats:
        queries.append(
            f"zö agency {rfp.client} {rfp.location or ''} {section.title} relevant experience"[:240]
        )

    if "voice" in cats:
        queries.append(
            f"zö agency narrative case study first person {section.title} {rfp.sector}"[:240]
        )

    if rfp_section and "placeholder" in cats:
        for req in (rfp_section.requirements or [])[:2]:
            queries.append(f"zö agency {req} {rfp.client}"[:240])

    seen: set[str] = set()
    cleaned: list[str] = []
    for query in queries:
        key = query.strip().lower()
        if key and key not in seen:
            seen.add(key)
            cleaned.append(query.strip())
    return cleaned[:4]


async def _warm_shared_evidence(
    *,
    rfp: RfpRecord,
    research: ProposalResearchCache | None,
    anchor_section_id: str,
    searched_queries: set[str],
) -> tuple[list[EvidenceItem], ProposalResearchCache | None, str]:
    """One shared Supermemory + KB pass per iteration (not per section)."""
    corpus = list((research.evidence_corpus if research else []) or [])
    queries = _base_pass_queries(rfp)
    kb_block = ""

    if supermemory.is_configured():
        for query in queries:
            key = query.strip().lower()
            if key in searched_queries:
                continue
            searched_queries.add(key)
            hits = await _search_hits(query)
            if hits:
                corpus = _merge_hits_into_corpus(corpus, hits, anchor_section_id)

    if not kb_block.strip():
        kb_block = await _fetch_kb_excerpts(queries[:2])

    updated_research = research
    if research and corpus != list(research.evidence_corpus or []):
        updated_research = research.model_copy(update={"evidence_corpus": corpus})

    return corpus, updated_research, kb_block


async def _search_hits(query: str) -> list[dict[str, Any]]:
    if not supermemory.is_configured():
        return []
    async with _search_semaphore:
        try:
            hits = await supermemory.search_documents(
                query=query,
                limit=SEARCH_LIMIT,
                include_full_docs=True,
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
            return [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
        except supermemory.SupermemoryError:
            return []


def _next_evidence_id(corpus: list[EvidenceItem]) -> int:
    max_id = 0
    for item in corpus:
        match = re.match(r"E(\d+)$", item.id)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return max_id + 1


def _merge_hits_into_corpus(
    corpus: list[EvidenceItem],
    hits: list[dict[str, Any]],
    section_id: str,
) -> list[EvidenceItem]:
    by_key = {item.chunk_key: item for item in corpus if item.chunk_key}
    counter = _next_evidence_id(corpus)
    updated = list(corpus)

    for hit in hits:
        key = _hit_key(hit)
        if key in by_key:
            existing = by_key[key]
            if section_id not in existing.section_ids:
                new_ids = [*existing.section_ids, section_id]
                by_key[key] = existing.model_copy(update={"section_ids": new_ids})
                updated = [
                    by_key[key] if item.id == existing.id else item for item in updated
                ]
            continue
        eid = f"E{counter}"
        counter += 1
        item = EvidenceItem(
            id=eid,
            source=_hit_label(hit),
            excerpt=_hit_excerpt(hit, max_chars=EXCERPT_MAX_CHARS),
            sectionIds=[section_id],
            chunkKey=key,
        )
        by_key[key] = item
        updated.append(item)

    return updated


def _evidence_for_section(section_id: str, corpus: list[EvidenceItem]) -> list[EvidenceItem]:
    tagged = [item for item in corpus if section_id in item.section_ids]
    if tagged:
        return tagged[:16]
    return corpus[:8]


def _format_evidence(items: list[EvidenceItem]) -> str:
    lines = []
    for item in items:
        lines.append(f"[{item.id}] {item.source}\n{item.excerpt[:1800]}")
    return "\n\n".join(lines) if lines else "(No evidence yet — use only what is already in the section.)"


async def _fetch_kb_excerpts(queries: list[str]) -> str:
    parts: list[str] = []
    for query in queries[:4]:
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(query, limit=6)
        if text.strip():
            parts.append(text[:3500])
    if not parts and queries:
        text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
            queries[0],
            limit=8,
        )
        if text.strip():
            parts.append(text[:4000])
    return "\n---\n".join(parts)[:12000]


async def _enrich_section_evidence(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    research: ProposalResearchCache | None,
    issues: list[PreSubmitIssue],
    rfp_section: RfpSectionMap | None,
    shared_kb_block: str = "",
    searched_queries: set[str] | None = None,
) -> tuple[list[EvidenceItem], str, ProposalResearchCache | None, list[str]]:
    """Section-specific retrieval only — shared corpus warmed once per pass."""
    methods: list[str] = []
    queries = _section_specific_queries(
        section=section,
        rfp=rfp,
        issues=issues,
        rfp_section=rfp_section,
    )
    corpus = list((research.evidence_corpus if research else []) or [])
    kb_block = shared_kb_block
    query_cache = searched_queries if searched_queries is not None else set()

    if section.id in STATIC_SECTION_IDS:
        static_queries = _base_pass_queries(rfp) + queries
        kb_block = await _fetch_kb_excerpts(static_queries[:4])
        if kb_block.strip():
            methods.append("kb_search")
        evidence = _evidence_for_section(section.id, corpus)
        return evidence, kb_block, research, methods

    if supermemory.is_configured() and queries:
        for query in queries:
            key = query.strip().lower()
            if key in query_cache:
                continue
            query_cache.add(key)
            hits = await _search_hits(query)
            if hits:
                corpus = _merge_hits_into_corpus(corpus, hits, section.id)
                if "supermemory" not in methods:
                    methods.append("supermemory")
        if research and corpus != list(research.evidence_corpus or []):
            research = research.model_copy(update={"evidence_corpus": corpus})

    evidence = _evidence_for_section(section.id, corpus)
    return evidence, kb_block, research, methods


async def _llm_surgical_fix(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    issues: list[PreSubmitIssue],
    content: str,
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
    evidence: list[EvidenceItem],
    kb_block: str,
    rfp_context: str,
    avoidance_block: str,
) -> tuple[str, str | None]:
    register = classify_section_register(
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    voice_block = format_brand_voice_block(
        brand_voice,
        kb_zo_voice=kb_zo_voice,
        rfp_client=rfp.client,
        register=register,
    )

    evidence_block = _format_evidence(evidence)
    if kb_block.strip():
        evidence_block += f"\n\nAdditional KB excerpts:\n{kb_block[:10000]}"

    user_block = f"""BRAND VOICE (mandatory — preserve throughout):
{voice_block}

Target client: {rfp.client}
RFP: {rfp.title}
Sector: {rfp.sector}
Section: {section.title}
Register: {register}
Word target: {section.word_target}

Issues to fix:
{_issues_summary(issues)}

Current section (preserve structure and strong prose):
{content[:14000]}

RFP context:
{rfp_context[:3500]}

Evidence corpus:
{evidence_block}
"""
    if avoidance_block:
        user_block += f"\n{avoidance_block}\n"

    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": SURGICAL_FIX_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=4096,
            temperature=0.2,
        )
    except LlmError:
        return content, None

    new_content = raw.get("content")
    if not isinstance(new_content, str) or not new_content.strip():
        return content, None

    new_content = enforce_narrative_voice(
        new_content.strip(),
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    return new_content, provider


def _patch_section_in_draft(
    draft: ProposalDraft,
    section_id: str,
    content: str,
) -> ProposalDraft:
    now = datetime.now(timezone.utc).isoformat()
    sections = [
        s.model_copy(update={"content": content}) if s.id == section_id else s
        for s in draft.sections
    ]
    return draft.model_copy(update={"sections": sections, "updated_at": now})


async def _cancelled(should_cancel: CancelCheck | None) -> bool:
    if should_cancel is None:
        return False
    return await should_cancel()


async def run_presubmit_autofix_loop(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    use_llm: bool = True,
    should_cancel: CancelCheck | None = None,
) -> tuple[ProposalDraft, PreSubmitReview, list[SectionAutoFixLog], str, int, ProposalResearchCache | None]:
    """Fix all affected sections — deterministic first, then AI + Supermemory when use_llm."""
    working = draft
    working_research = research
    fix_logs: list[SectionAutoFixLog] = []
    prev_fingerprint: str | None = None
    stopped_reason = "max_iterations"
    max_iterations = MAX_ITERATIONS_LLM if use_llm else MAX_ITERATIONS_DETERMINISTIC

    rfp_context = (
        f"RFP: {rfp.title}\nClient: {rfp.client}\nSector: {rfp.sector}\n"
        f"Location: {rfp.location or '(not specified)'}"
    )
    brand_voice_raw = working_research.brand_voice if working_research else None
    bv_dict: dict[str, Any] | None = None
    if brand_voice_raw is not None:
        bv_dict = brand_voice_raw.model_dump(by_alias=True)
    brand_voice, kb_zo_voice = await resolve_voice_context(
        rfp=rfp,
        rfp_context=rfp_context,
        brand_voice=bv_dict,
    )
    avoidance_block = ""
    if working_research and working_research.writing_avoidances:
        avoidance_block = format_avoidance_block(working_research.writing_avoidances)

    initial_review = run_presubmit_review(rfp=rfp, draft=working, research=working_research)

    if initial_review.ready_to_submit:
        return working, initial_review, fix_logs, "ready", 0, working_research

    iterations_run = 0
    for iteration in range(1, max_iterations + 1):
        if await _cancelled(should_cancel):
            stopped_reason = "cancelled"
            save_proposal_draft(working)
            review = run_presubmit_review(rfp=rfp, draft=working, research=working_research)
            return working, review, fix_logs, stopped_reason, iterations_run, working_research

        iterations_run = iteration
        review = run_presubmit_review(rfp=rfp, draft=working, research=working_research)
        fingerprint = _issue_fingerprint(review)

        if review.ready_to_submit:
            stopped_reason = "ready"
            return working, review, fix_logs, stopped_reason, iterations_run, working_research

        if fingerprint == prev_fingerprint:
            stopped_reason = "converged"
            return working, review, fix_logs, stopped_reason, iterations_run, working_research

        grouped = _group_issues_by_section(review.issues)
        if not grouped:
            stopped_reason = "no_fixable_issues"
            return working, review, fix_logs, stopped_reason, iterations_run, working_research

        issues_at_start = len(review.issues)
        patched_this_pass = 0

        section_ids = sorted(
            grouped.keys(),
            key=lambda sid: _issue_sort_key(grouped[sid][0]),
        )
        total_sections = len(section_ids)
        searched_queries: set[str] = set()
        shared_kb_block = ""

        if use_llm and llm.is_configured() and section_ids:
            logger.info(
                "Auto-fix pass %d/%d: warming shared KB search for %d sections",
                iteration,
                max_iterations,
                total_sections,
            )
            _, working_research, shared_kb_block = await _warm_shared_evidence(
                rfp=rfp,
                research=working_research,
                anchor_section_id=section_ids[0],
                searched_queries=searched_queries,
            )

        for section_index, section_id in enumerate(section_ids, start=1):
            if await _cancelled(should_cancel):
                stopped_reason = "cancelled"
                save_proposal_draft(working)
                review = run_presubmit_review(rfp=rfp, draft=working, research=working_research)
                return working, review, fix_logs, stopped_reason, iterations_run, working_research

            section = next((s for s in working.sections if s.id == section_id), None)
            if not section or not section.content.strip():
                continue

            section_issues = grouped[section_id]
            original = section.content
            content, methods = _apply_deterministic_fixes(section, rfp)
            rfp_section = _find_rfp_section(working_research, section_id)

            if use_llm and llm.is_configured():
                logger.info(
                    "Auto-fix pass %d: section %d/%d — %s (%d issues)",
                    iteration,
                    section_index,
                    total_sections,
                    section.title,
                    len(section_issues),
                )
                evidence, kb_block, working_research, retrieval_methods = (
                    await _enrich_section_evidence(
                        section=section,
                        rfp=rfp,
                        research=working_research,
                        issues=section_issues,
                        rfp_section=rfp_section,
                        shared_kb_block=shared_kb_block,
                        searched_queries=searched_queries,
                    )
                )
                for m in retrieval_methods:
                    if m not in methods:
                        methods.append(m)

                repaired, provider = await _llm_surgical_fix(
                    section=section,
                    rfp=rfp,
                    issues=section_issues,
                    content=content,
                    brand_voice=brand_voice,
                    kb_zo_voice=kb_zo_voice,
                    evidence=evidence,
                    kb_block=kb_block,
                    rfp_context=rfp_context,
                    avoidance_block=avoidance_block,
                )
                if provider:
                    methods.append("llm_repair")
                content = repaired

            if content == original:
                continue

            working = _patch_section_in_draft(working, section_id, content)
            patched_this_pass += 1
            fix_logs.append(
                SectionAutoFixLog(
                    section_id=section_id,
                    section_title=section.title,
                    iteration=iteration,
                    methods=methods,
                    issues_targeted=len(section_issues),
                )
            )

        save_proposal_draft(working)

        review_after = run_presubmit_review(rfp=rfp, draft=working, research=working_research)
        prev_fingerprint = fingerprint

        if len(review_after.issues) >= issues_at_start and patched_this_pass == 0:
            stopped_reason = "no_progress"
            return working, review_after, fix_logs, stopped_reason, iterations_run, working_research

        if review_after.ready_to_submit:
            stopped_reason = "ready"
            return working, review_after, fix_logs, stopped_reason, iterations_run, working_research

        issues_at_start = len(review_after.issues)

    final_review = run_presubmit_review(rfp=rfp, draft=working, research=working_research)
    return working, final_review, fix_logs, stopped_reason, iterations_run, working_research
