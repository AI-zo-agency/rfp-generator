"""Complete & Clean — async per-section KB line grounding.

Never invents. For each section an agent plans Supermemory queries from the
claims in that tab, retrieves evidence concurrently, then removes or demotes
ungrounded factual claims. Placeholders stay only when the RFP requires them.
Company-info dumps that already live in Who We Are / 1.3 become a cross-ref.
zö voice only — no AI-slop expansion.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_rfp_excerpt import build_priority_rfp_excerpt
from app.services.proposal_section_health import is_dead_section
from app.services.proposal_verify_optional_scrub import (
    count_placeholder_tags,
    scrub_result_introduces_fabrication,
    strip_placeholder_tags_not_required_by_rfp,
)
from app.services.proposal_voice_enforcement import enforce_narrative_voice

logger = logging.getLogger(__name__)

SECTION_PARALLEL = 4
QUERY_PARALLEL = 3
MAX_QUERIES = 4
MIN_SECTION_CHARS = 80
MAX_SECTION_CHARS_FOR_LLM = 14_000
MAX_KB_CHARS = 10_000

_CANONICAL_COMPANY_TITLE_RE = re.compile(
    r"(?i)\b("
    r"who\s+we\s+are|business\s+information|company\s+overview|"
    r"about\s+(?:the\s+)?(?:firm|agency|company)|organizational\s+overview"
    r")\b"
)


@dataclass
class LineGroundReport:
    sections_checked: int = 0
    sections_changed: int = 0
    queries_run: int = 0
    logs: list[str] = field(default_factory=list)


def _canonical_company_excerpt(sections: list[ProposalSection]) -> str:
    """Short excerpt from the manuscript's primary company-info tab (if any)."""
    for section in sections:
        title = section.title or ""
        if not _CANONICAL_COMPANY_TITLE_RE.search(title):
            continue
        body = (section.content or "").strip()
        if len(body) < 40:
            continue
        return f"### Canonical company tab: {title}\n{body[:2_500]}"
    return ""


async def _plan_queries_for_section(
    section: ProposalSection,
    *,
    rfp: RfpRecord,
) -> list[str]:
    """Agent plans Supermemory queries from section claims — no invented anchors."""
    body = (section.content or "").strip()
    if not body or not llm.is_configured():
        title = (section.title or "").strip()
        return [f"zö agency {title}"[:200]] if title else []

    system = (
        "You are zö agency's Complete-Scan query planner.\n"
        "Read the proposal section and plan Supermemory search queries that will "
        "confirm or refute the FACTUAL claims in it (people, tools delivered, "
        "certs, clients, metrics, insurance, contacts).\n"
        "Rules:\n"
        "- Max 4 queries. Prefer 01_companyfacts / 03_CS / 04_Bio / Guide_Pricing "
        "style phrasing as zö materials are written.\n"
        "- Do NOT invent client- or person-specific anchors that are not already "
        "named in the section.\n"
        "- Do NOT use the RFP buyer's name as the search subject.\n"
        "- Skip pure logistics fluff; focus on checkable facts.\n"
        "Return JSON only: {\"queries\":[\"…\",\"…\"]}"
    )
    user = (
        f"RFP client (context only, not a search subject): {rfp.client or 'n/a'}\n"
        f"Section title: {section.title or ''}\n\n"
        f"Section body (truncate ok):\n{body[:8_000]}\n"
    )
    try:
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=800,
            temperature=0.1,
            tier="light",
            node_name="scan_line_ground_plan",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Query plan failed for %s", section.id, exc_info=True)
        return [f"zö agency {section.title or ''}"[:200]]

    queries: list[str] = []
    if isinstance(raw, dict):
        for item in raw.get("queries") or []:
            q = re.sub(r"\s+", " ", str(item or "")).strip()
            if q and q.casefold() not in {x.casefold() for x in queries}:
                queries.append(q[:220])
            if len(queries) >= MAX_QUERIES:
                break
    if not queries:
        queries = [f"zö agency {section.title or ''}"[:200]]
    return queries


async def _retrieve_kb(queries: list[str]) -> tuple[str, list[str]]:
    from app.services.kb_rag_retrieve import retrieve_for_question

    if not queries:
        return "", []

    sem = asyncio.Semaphore(QUERY_PARALLEL)

    async def _one(query: str) -> tuple[str, list[str]]:
        async with sem:
            try:
                ctx, labels, _ = await retrieve_for_question(
                    query,
                    limit=4,
                    max_chars=4_000,
                    threshold=0.32,
                )
                return ctx, labels
            except Exception:  # noqa: BLE001
                logger.warning("KB retrieve failed for %r", query[:80], exc_info=True)
                return "", []

    results = await asyncio.gather(*[_one(q) for q in queries])
    parts: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    for query, (ctx, labels) in zip(queries, results):
        if not ctx or ctx.startswith("(No matching"):
            continue
        parts.append(f"### Retrieval: {query}\n{ctx}")
        for label in labels:
            key = label.casefold()
            if key not in seen:
                seen.add(key)
                sources.append(label)
    blob = "\n\n".join(parts)
    if len(blob) > MAX_KB_CHARS:
        blob = blob[:MAX_KB_CHARS]
    return blob, sources


async def _ground_section_body(
    section: ProposalSection,
    *,
    kb_context: str,
    rfp_text: str,
    company_excerpt: str,
) -> tuple[str, str]:
    """LLM: remove ungrounded claims; never invent; no expansion."""
    body = section.content or ""
    if not llm.is_configured():
        cleaned, _ = strip_placeholder_tags_not_required_by_rfp(body, rfp_text)
        return cleaned, "LLM off — deterministic placeholder scrub only."

    rfp_excerpt = build_priority_rfp_excerpt(rfp_text or "", max_chars=8_000)
    system = (
        "You are zö agency's Complete-Scan line-grounding editor.\n"
        "Verify this proposal SECTION against KB evidence. You do NOT generate a "
        "new section and you do NOT invent facts.\n"
        "HARD RULES:\n"
        "1. NEVER invent names, phones, emails, dollars, certs, clients, wins, "
        "tools delivered, or bio years. If KB lacks evidence, DELETE the claim or "
        "keep a short [MANUAL FILL: Sonja — field] ONLY when the RFP explicitly "
        "requires that field for DQ/scoring; otherwise delete.\n"
        "2. Prefer DELETE / shorten over rewrite. Do not expand word count. No "
        "AI-slop (delve, leverage synergies, robust end-to-end, passionate about).\n"
        "3. If company legal name / FEIN / address / contact dump already appears "
        "in the CANONICAL company tab excerpt, replace restated dumps here with a "
        "one-line cross-ref to that tab (e.g. 'See Who We Are / Business "
        "Information for legal entity details.') — do not repeat the table.\n"
        "4. Fill a [VERIFY]/[MANUAL FILL] ONLY with a verbatim fact from KB "
        "evidence. If KB is silent and RFP does not require it → remove the tag "
        "and clean the sentence.\n"
        "5. Keep zö voice: concrete, human, short. Preserve tables/structure.\n"
        "6. Return JSON only."
    )
    user = (
        f"Section title: {section.title or ''}\n\n"
        f"RFP excerpt (what is required):\n{rfp_excerpt or '(none)'}\n\n"
        f"Canonical company tab (do not restate):\n"
        f"{company_excerpt or '(none found)'}\n\n"
        f"KB evidence:\n{kb_context or '(no KB hits)'}\n\n"
        f"Section body:\n{body[:MAX_SECTION_CHARS_FOR_LLM]}\n\n"
        "Return JSON:\n"
        '{"content":"full updated markdown","note":"one short sentence"}\n'
    )
    try:
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=6_000,
            temperature=0.1,
            tier="light",
            node_name="scan_line_ground_edit",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Line ground LLM failed for %s", section.id, exc_info=True)
        cleaned, _ = strip_placeholder_tags_not_required_by_rfp(body, rfp_text)
        return cleaned, "grounding LLM failed — deterministic scrub only."

    updated = ""
    note = ""
    if isinstance(raw, dict):
        updated = str(raw.get("content") or raw.get("updatedContent") or "").strip()
        note = str(raw.get("note") or "").strip()
    if not updated:
        cleaned, _ = strip_placeholder_tags_not_required_by_rfp(body, rfp_text)
        return cleaned, "empty grounding rewrite — deterministic scrub only."

    # Reject wipe / huge expansion / invented contacts.
    if len(updated) < max(24, int(len(body) * 0.25)):
        return body, "rejected truncated rewrite."
    if len(updated) > int(len(body) * 1.35) + 400:
        return body, "rejected expansion rewrite."
    if scrub_result_introduces_fabrication(
        body,
        updated,
        rfp_text=rfp_excerpt or rfp_text or "",
        kb_text=kb_context or "",
    ):
        return body, "rejected — invented contact/money facts."

    updated, _ = strip_placeholder_tags_not_required_by_rfp(updated, rfp_text)
    updated = enforce_narrative_voice(
        updated,
        section_id=section.id or "",
        title=section.title or "",
    )
    return updated, note or "line-grounded against KB."


async def _ground_one(
    section: ProposalSection,
    *,
    rfp: RfpRecord,
    rfp_text: str,
    company_excerpt: str,
) -> tuple[ProposalSection, int, str]:
    body = section.content or ""
    if is_dead_section(body) or len(body.strip()) < MIN_SECTION_CHARS:
        return section, 0, ""

    queries = await _plan_queries_for_section(section, rfp=rfp)
    kb_context, sources = await _retrieve_kb(queries)
    updated, note = await _ground_section_body(
        section,
        kb_context=kb_context,
        rfp_text=rfp_text,
        company_excerpt=company_excerpt,
    )
    q_n = len(queries)
    if updated.strip() == body.strip():
        return section, q_n, note
    return (
        section.model_copy(update={"content": updated}),
        q_n,
        note
        + (f" [KB: {', '.join(sources[:3])}]" if sources else ""),
    )


async def run_scan_line_grounding_pass(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, LineGroundReport]:
    """Async per-section KB verification for Complete & Clean. Never invents."""
    del research  # reserved for future mapped-requirement focus
    from app.services.proposal_budget_content import section_is_budgetish
    from app.services.proposal_consistency_enforcement import (
        polish_schedule_tabs_for_designer,
    )

    report = LineGroundReport()
    polished, polish_logs = polish_schedule_tabs_for_designer(
        list(draft.sections), rfp_text=rfp_text
    )
    if polish_logs:
        draft = draft.model_copy(update={"sections": polished})
        report.logs.extend(polish_logs)

    sections = list(draft.sections)
    company_excerpt = _canonical_company_excerpt(sections)
    work: list[tuple[int, ProposalSection]] = []
    for idx, section in enumerate(sections):
        # Not excluded from grounding even though it's a static company-fact
        # section (org-structure, business-info, certifications, insurance):
        # this pass verifies claims against current KB evidence, which is
        # exactly what catches a roster title gone stale against a standing
        # correction (e.g. a title change) — skipping it here would silently
        # leave that kind of error uncorrected.
        body = section.content or ""
        if is_dead_section(body):
            continue
        if section_is_budgetish(section):
            continue
        if len(body.strip()) < MIN_SECTION_CHARS and count_placeholder_tags(body) <= 0:
            continue
        work.append((idx, section))

    if not work:
        return draft, report

    sem = asyncio.Semaphore(SECTION_PARALLEL)

    async def _run(idx: int, section: ProposalSection) -> tuple[int, ProposalSection, int, str]:
        async with sem:
            updated, q_n, note = await _ground_one(
                section,
                rfp=rfp,
                rfp_text=rfp_text,
                company_excerpt=company_excerpt,
            )
            return idx, updated, q_n, note

    results = await asyncio.gather(*[_run(i, s) for i, s in work])
    out = list(sections)
    for idx, updated, q_n, note in results:
        report.sections_checked += 1
        report.queries_run += q_n
        before = sections[idx].content or ""
        if (updated.content or "") != before:
            out[idx] = updated
            report.sections_changed += 1
            title = updated.title or updated.id
            report.logs.append(
                f"line-ground:{title}: {note or 'updated'} ({q_n} queries)"
            )
        elif note:
            report.logs.append(
                f"line-ground:{(updated.title or updated.id)}: {note} ({q_n} queries)"
            )

    if report.sections_changed:
        draft = draft.model_copy(update={"sections": out})
    return draft, report
