"""Post-generation KB fact checker — cross-verify every section against Supermemory.

Uses the same retrieval stack as kb_qa_loop (memories + chunks, full-doc pack).
Runs after Sections 1–3, Phase 3 drafting, and at the start of self-edit.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from typing import Any

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.kb_rag_retrieve import _question_terms, retrieve_for_question
from app.services.proposal_brand_voice import classify_section_register, format_brand_voice_block
from app.services.proposal_drafting_prompts import ANTI_HALLUCINATION_RULES
from app.services.proposal_manual_flags import _replace_verify_tags_from_blob
from app.services.proposal_section_quality import word_count

logger = logging.getLogger(__name__)

# Cap concurrent section fact-checks / KB queries (I/O bound; avoid rate-limit storms).
FACT_CHECK_SECTION_PARALLEL = 4
FACT_CHECK_KB_QUERY_PARALLEL = 4

_FALSE_VERIFY_STUB_RE = re.compile(
    r"\[VERIFY:\s*Draft content for .+?insufficient evidence in corpus",
    re.I,
)
_INSUFFICIENT_EVIDENCE_RE = re.compile(r"insufficient evidence in corpus", re.I)
_WHOLE_SECTION_DRAFT_STUB_RE = re.compile(
    r"^\[VERIFY:\s*Draft content for .+ — (?:insufficient evidence in corpus|writer returned empty prose)",
    re.I | re.S,
)
_TRANSmittal_TITLE_HINTS = (
    "cover letter",
    "executive summary",
    "letter of transmittal",
    "transmittal letter",
)
_PLAN_DRIVEN_SKIP_FACT_CHECK_HINTS = (
    "mockup",
    "wireframe",
    "conceptual",
    "homepage design",
    "cover letter",
    "executive summary",
    "letter of transmittal",
    "transmittal letter",
    "project schedule",
    "timeline",
    "work plan",
)
_EVAL_SCORE_CONTEXT_RE = re.compile(
    r"(evaluat|score|weight|criteria|points|best value|ranked)",
    re.I,
)
_PERCENT_RE = re.compile(r"\b(\d{1,3})\s*%")


def _is_whole_section_draft_stub(content: str) -> bool:
    stripped = (content or "").strip()
    return bool(_WHOLE_SECTION_DRAFT_STUB_RE.match(stripped)) or bool(
        _FALSE_VERIFY_STUB_RE.search(stripped) and word_count(stripped) < 80
    )


def _substantive_prose(content: str, *, min_words: int = 50) -> bool:
    body = (content or "").strip()
    if not body or _is_whole_section_draft_stub(body):
        return False
    return word_count(body) >= min_words


def _reject_destructive_fact_check_rewrite(prior: str, proposed: str) -> bool:
    """True when the agent output must be discarded and the prior draft kept."""
    prior_s = (prior or "").strip()
    prop_s = (proposed or "").strip()
    if not prop_s or prop_s == prior_s:
        return False
    if _substantive_prose(prior_s) and _is_whole_section_draft_stub(prop_s):
        return True
    if _substantive_prose(prior_s, min_words=80) and word_count(prop_s) < 40:
        return True
    return False


@dataclass
class FactCheckReport:
    sections_checked: int = 0
    verify_tags_filled: int = 0
    stubs_repaired: int = 0
    eval_repairs: int = 0
    requirement_repairs: int = 0
    duplicates_removed: int = 0
    metric_flags: int = 0
    logs: list[str] = field(default_factory=list)


def _is_legal_attestation_section(section: ProposalSection) -> bool:
    """E-Verify / disclosure / affidavit — never let LLM re-assert as settled fact."""
    title = (section.title or "").casefold()
    body = (section.content or "")[:900].casefold()
    hints = (
        "e-verify",
        "affidavit",
        "disclosure statement",
        "conflict of interest",
        "contractor affidavit",
        "penalty of perjury",
        "non-collusion",
    )
    return any(h in title or h in body for h in hints)


def _priority_kb_queries(
    section: ProposalSection,
    *,
    rfp: RfpRecord,
    rfp_context: str = "",
) -> list[str]:
    """Deterministic queries for known legal / critique gaps (not title mash / LLM filler)."""
    from app.services.evidence_trust.legal_attestation_gate import (
        rfp_needs_health_coalition_proof,
    )

    title = (section.title or "").casefold()
    body = (section.content or "").casefold()
    sid = section.id or ""
    out: list[str] = []

    if _is_legal_attestation_section(section) or "e-verify" in title or "e-verify" in body:
        out.append("01_companyfacts zö agency E-Verify enrollment compliance")
        out.append("01_companyfacts zö agency federal contractor certifications affidavits")

    if "disclosure" in title or "conflict" in title or "conflict of interest" in body:
        out.append("01_companyfacts zö agency conflict of interest disclosure")

    if any(
        h in title
        for h in ("staffing", "personnel", "hours", "cost proposal", "budget", "pricing")
    ) or re.search(r"\b(?:400|320|280|200|160)\s*hours?\b", body):
        out.append("01_PricingGuide zö agency staffing hours rates annual allocation")
        out.append("01_companyfacts zö agency pricing guide project management hours")

    if "10-year" in body or "corporate-creative" in body or "who we are" in title:
        out.append("01_companyfacts zö agency founded August 21 2013 years in business")

    refs_or_exp = any(
        h in title
        for h in (
            "reference",
            "previous experience",
            "past performance",
            "case stud",
            "our work",
            "relevant experience",
        )
    ) or sid.startswith("section-3")
    if rfp_needs_health_coalition_proof(rfp, rfp_context) and refs_or_exp:
        out.append(
            "03_CS Recovery Network of Oregon RNO Oregon Recovers coalition stigma health"
        )
        out.append(
            "Recovery Network of Oregon zö agency case study outcomes references contact"
        )

    seen: set[str] = set()
    deduped: list[str] = []
    for q in out:
        key = q.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(q)
    return deduped


def _merge_query_lists(*groups: list[str], limit: int = 6) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for q in group:
            text = (q or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text[:240])
            if len(merged) >= limit:
                return merged
    return merged


def _eval_percent_claimed_without_rfp(content: str, rfp_text: str) -> list[str]:
    """Return percentage strings cited as scoring weights but absent from RFP text."""
    if not (content or "").strip():
        return []
    rfp_cf = (rfp_text or "").casefold()
    bad: list[str] = []
    for match in _PERCENT_RE.finditer(content):
        pct = match.group(1)
        start = max(0, match.start() - 120)
        end = min(len(content), match.end() + 80)
        local = content[start:end]
        if not _EVAL_SCORE_CONTEXT_RE.search(local):
            continue
        if pct in rfp_cf or f"{pct}%" in rfp_cf:
            continue
        bad.append(f"{pct}%")
    return bad


async def _repair_eval_percentages(
    section: ProposalSection,
    rfp_excerpt: str,
) -> tuple[ProposalSection, bool]:
    bad = _eval_percent_claimed_without_rfp(section.content or "", rfp_excerpt)
    if not bad or not llm.is_configured():
        return section, False
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You fix fabricated evaluation weights in proposal sections.\n"
                        "If the RFP uses ranked Best Value (no point percentages), remove ALL "
                        "invented percentage weights and restate criteria in the RFP's order and "
                        "exact category names from the excerpt only.\n"
                        "Never invent new percentages. Never rename criteria to generic labels "
                        "like 'Technical Approach' unless the RFP uses that phrase.\n"
                        "Return JSON: {\"content\": \"full section markdown\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Section: {section.title}\n"
                        f"Unsupported weight claims: {', '.join(bad)}\n\n"
                        f"RFP excerpt:\n{rfp_excerpt[:45000]}\n\n"
                        f"Current section:\n{(section.content or '')[:12000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.0,
        )
        content = str((raw or {}).get("content") or "").strip()
        if content and content != (section.content or "").strip():
            return section.model_copy(update={"content": content}), True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eval-percent repair failed for %s: %s", section.id, exc)
    return section, False


def _resolve_mapped_section(
    section: ProposalSection,
    research: ProposalResearchCache | None,
) -> RfpSectionMap | None:
    if not research or not research.rfp_sections:
        return None
    sid = section.id
    for mapped in research.rfp_sections:
        if mapped.id == sid:
            return mapped
    for mapped in research.rfp_sections:
        dup = mapped.duplicate_of_static_section
        if dup and dup == sid:
            return mapped
    title_cf = (section.title or "").casefold().strip()
    if title_cf:
        for mapped in research.rfp_sections:
            if (mapped.title or "").casefold().strip() == title_cf:
                return mapped
    return None


def _requirements_for_section(
    section: ProposalSection,
    mapped: RfpSectionMap | None,
) -> list[str]:
    reqs: list[str] = []
    if mapped:
        reqs.extend(list(mapped.requirements or []))
        for gap in mapped.uncovered_requirements or []:
            g = str(gap).strip()
            if g:
                reqs.append(f"(coverage gap) {g}")
    if reqs:
        return reqs
    sid = section.id
    if sid.startswith("section-1"):
        return [
            "Agency identity, differentiators, and credentials required by RFP Section 1",
            "Use only verified company facts and certifications from KB",
        ]
    if sid.startswith("section-2"):
        return [
            "Team roles and relevant experience per RFP",
            "Personnel names only from approved 04_Bio files in KB",
        ]
    if sid.startswith("section-3-work"):
        return [
            "Verified case study aligned to RFP sector/scope",
            "Outcomes and metrics only if verbatim in 03_CS or won proposal KB",
        ]
    title = (section.title or "this section").strip()
    return [f"Address all RFP requirements for: {title}"]


def _rfp_excerpt_for_section(
    rfp_context: str,
    *,
    section_title: str,
    requirements: list[str],
    max_chars: int = 35_000,
) -> str:
    """Pull RFP paragraphs most relevant to this section's requirements."""
    text = (rfp_context or "").strip()
    if not text:
        return ""
    terms = set(_question_terms(" ".join(requirements) + " " + section_title))
    title_words = [
        w for w in re.split(r"\W+", (section_title or "").casefold()) if len(w) >= 4
    ]
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    scored: list[tuple[int, str]] = []
    for para in paras:
        cf = para.casefold()
        score = sum(1 for t in terms if len(t) >= 3 and t in cf)
        score += sum(2 for w in title_words if w in cf)
        if score > 0:
            scored.append((score, para))
    scored.sort(key=lambda item: -item[0])
    parts: list[str] = []
    total = 0
    for _, para in scored:
        if total + len(para) + 2 > max_chars:
            remaining = max_chars - total - 2
            if remaining > 400:
                parts.append(para[:remaining])
            break
        parts.append(para)
        total += len(para) + 2
    if parts:
        return "\n\n".join(parts)
    return text[:max_chars]


def _brand_voice_payload(research: ProposalResearchCache | None) -> dict[str, Any] | None:
    if not research or not research.brand_voice:
        return None
    return research.brand_voice.model_dump(by_alias=True)


def _member_name_from_bio_section(title: str) -> str:
    return _client_from_section_title(title)


def _split_bio_subsections(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Split Section 2 bio markdown on **Heading** lines."""
    preamble_parts: list[str] = []
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_body
        if current_heading:
            sections.append((current_heading, "\n".join(current_body).strip()))
        current_heading = ""
        current_body = []

    for line in (content or "").splitlines():
        m = re.match(r"^\*\*([^*]+)\*\*\s*$", line.strip())
        if m:
            if not current_heading and current_body:
                preamble_parts.extend(current_body)
                current_body = []
            elif current_heading:
                flush()
            current_heading = m.group(1).strip()
            current_body = [line]
        else:
            current_body.append(line)
    if current_heading:
        flush()
    elif current_body:
        preamble_parts.extend(current_body)
    return "\n".join(preamble_parts).strip(), sections


def _kb_query_for_bio_subsection(member: str, heading: str) -> str:
    h = heading.casefold()
    if "work" in h and "history" in h:
        return f"04_Bio {member} work history employment positions dates zö agency"[:240]
    if "education" in h:
        return f"04_Bio {member} education degree university college"[:240]
    if "experience" in h or "expertise" in h:
        return f"04_Bio {member} years experience area of expertise table"[:240]
    if "account" in h:
        return f"04_Bio {member} key accounts clients"[:240]
    if "license" in h:
        return f"04_Bio {member} licenses professional"[:240]
    if "certif" in h:
        return f"04_Bio {member} certifications"[:240]
    if "description" in h:
        return f"04_Bio {member} bio overview description zö agency"[:240]
    return f"04_Bio {member} {heading} zö agency"[:240]


async def _repair_bio_subsection_block(
    *,
    member: str,
    heading: str,
    block: str,
) -> tuple[str, int]:
    """Fill VERIFY tags in one bio subsection only; leave other subsections untouched."""
    if "[VERIFY:" not in block and not _INSUFFICIENT_EVIDENCE_RE.search(block):
        return block, 0

    query = _kb_query_for_bio_subsection(member, heading)
    kb_context, _, _ = await retrieve_for_question(
        query,
        limit=6,
        max_chars=28_000,
        threshold=0.32,
    )
    if kb_context.startswith("(No matching"):
        return block, 0

    updated, fills = _replace_verify_tags_from_blob(block, kb_context)
    if fills and "[VERIFY:" not in updated:
        return updated, fills
    if not llm.is_configured() or "[VERIFY:" not in updated:
        return updated, fills

    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        f"You complete ONE subsection ({heading!r}) of a team bio for {member} at zö agency.\n"
                        "Use ONLY the 04_Bio knowledge-base excerpt below.\n"
                        "Replace [VERIFY: …] with KB-backed bullets or prose. Keep the **heading** line.\n"
                        "Do not invent employers, dates, degrees, or clients. If KB lacks data, keep a specific "
                        "[VERIFY: field — reason] tag.\n"
                        'Return JSON: {"content": "markdown for this subsection only"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Subsection to fix:\n{updated[:6000]}\n\n"
                        f"Knowledge base (04_Bio):\n{kb_context[:35000]}"
                    ),
                },
            ],
            max_tokens=2048,
            temperature=0.0,
        )
        new_block = str((raw or {}).get("content") or "").strip()
        if new_block and new_block != block:
            if not new_block.lstrip().startswith("**"):
                new_block = f"**{heading}**\n{new_block}"
            extra = 1 if "[VERIFY:" not in new_block and "[VERIFY:" in block else 0
            return new_block, fills + extra
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bio subsection repair failed for %s / %s: %s", member, heading, exc)
    return updated, fills


async def _fact_check_bio_subsections(
    section: ProposalSection,
) -> tuple[ProposalSection, int, list[str]]:
    """Section 2 bios: only subsections that still contain VERIFY tags."""
    if not section.id.startswith("section-2-bio"):
        return section, 0, []

    member = _member_name_from_bio_section(section.title or "")
    preamble, blocks = _split_bio_subsections(section.content or "")
    if not blocks:
        return section, 0, []

    logs: list[str] = []
    total_fills = 0
    rebuilt: list[str] = []
    if preamble:
        rebuilt.append(preamble)

    for heading, block in blocks:
        if "[VERIFY:" not in block and not _INSUFFICIENT_EVIDENCE_RE.search(block):
            rebuilt.append(block)
            continue
        new_block, fills = await _repair_bio_subsection_block(
            member=member or section.title,
            heading=heading,
            block=block,
        )
        total_fills += fills
        if fills or new_block != block:
            logs.append(f"Bio subsection {heading!r} in {section.title}")
        rebuilt.append(new_block)

    if total_fills == 0 and not logs:
        return section, 0, []

    new_content = "\n\n".join(part for part in rebuilt if part.strip())
    return section.model_copy(update={"content": new_content}), total_fills, logs


def _should_run_requirement_agent(
    section: ProposalSection,
    mapped: RfpSectionMap | None,
    content: str,
) -> bool:
    body = content or ""
    title_cf = (section.title or "").casefold()
    if any(hint in title_cf for hint in _PLAN_DRIVEN_SKIP_FACT_CHECK_HINTS):
        if _substantive_prose(body) and "[VERIFY:" not in body:
            return False
    if _INSUFFICIENT_EVIDENCE_RE.search(body) or _FALSE_VERIFY_STUB_RE.search(body):
        return True
    if section.id.startswith("section-2-bio") and "[VERIFY:" in body:
        # Subsection-scoped bio repair handles VERIFY blocks without rewriting Key Accounts, etc.
        return False
    if "[VERIFY:" in body:
        return True
    if mapped and mapped.uncovered_requirements:
        return True
    title_cf = (section.title or "").casefold()
    if "5.2.1" in title_cf or (
        "specification" in title_cf and "compliance" in title_cf
    ):
        return True
    if mapped and mapped.requirements and mapped.zo_mode == "write":
        if word_count(body) < 80:
            return True
    return False


async def _plan_kb_queries(
    *,
    section: ProposalSection,
    requirements: list[str],
    retrieval_focus: list[str],
    rfp: RfpRecord,
    mapped: RfpSectionMap | None = None,
    rfp_context: str = "",
) -> list[str]:
    priority = _priority_kb_queries(section, rfp=rfp, rfp_context=rfp_context)
    planned: list[str] = []
    if llm.is_configured():
        try:
            from app.services.proposal_langchain_agents import (
                AgentRole,
                plan_section_queries_agent,
            )

            planned = await plan_section_queries_agent(
                role=AgentRole.QUERY_PLANNER,
                rfp_client=rfp.client,
                rfp_sector=rfp.sector,
                section_title=section.title or "",
                requirements=requirements,
                retrieval_focus=retrieval_focus,
                prior_queries=priority,
                user_message=(
                    "Fact-check pass: plan Supermemory queries from the mapped RFP "
                    "requirements and this section's draft gaps. Prefer specific "
                    "01_companyfacts / 03_CS / 04_Bio queries. For health/coalition RFPs "
                    "include Recovery Network of Oregon when experience/references are "
                    "needed. NEVER plan queries that would invent E-Verify enrollment or "
                    "conflict-free disclosures — those stay VERIFY unless companyfacts "
                    "explicitly confirm."
                ),
                current_content=section.content or "",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Query planner failed for %s: %s", section.id, exc)

    fallback: list[str] = []
    for req in requirements[:3]:
        q = f"zö agency {req}".strip()
        if q:
            fallback.append(q[:240])
    for focus in retrieval_focus[:2]:
        f = str(focus).strip()
        if f:
            fallback.append(f[:240])
    if not priority and not planned and not fallback:
        fallback.append(_kb_query_for_section(section, rfp, mapped=mapped))
    return _merge_query_lists(priority, planned or [], fallback, limit=6)


async def _gather_kb_context(
    queries: list[str],
    *,
    fallback_section: ProposalSection,
    rfp: RfpRecord,
    mapped: RfpSectionMap | None = None,
) -> tuple[str, list[str]]:
    if not queries:
        return await _kb_blob_for_section(fallback_section, rfp, mapped=mapped)

    sem = asyncio.Semaphore(FACT_CHECK_KB_QUERY_PARALLEL)

    async def _one(query: str) -> tuple[str, str, list[str]]:
        async with sem:
            context, sources, _ = await retrieve_for_question(
                query,
                limit=6,
                max_chars=18_000,
                threshold=0.32,
            )
            return query, context, sources

    results = await asyncio.gather(*[_one(q) for q in queries])
    seen_sources: set[str] = set()
    parts: list[str] = []
    all_sources: list[str] = []
    for query, context, sources in results:
        if not context or context.startswith("(No matching"):
            continue
        parts.append(f"### Retrieval: {query}\n{context}")
        for label in sources:
            if label not in seen_sources:
                seen_sources.add(label)
                all_sources.append(label)
    merged = "\n\n".join(parts).strip()
    if not merged:
        return await _kb_blob_for_section(fallback_section, rfp, mapped=mapped)
    if len(merged) > 48_000:
        merged = merged[:48_000]
    return merged, all_sources


async def _run_requirement_aligned_fact_check_agent(
    section: ProposalSection,
    *,
    requirements: list[str],
    retrieval_focus: list[str],
    rfp_excerpt: str,
    kb_context: str,
    rfp: RfpRecord,
    brand_voice: dict[str, Any] | None,
) -> tuple[ProposalSection, bool, str]:
    """Read requirements → RFP excerpt → KB; rewrite only when gaps or fabrications exist."""
    if not llm.is_configured():
        return section, False, ""
    register = classify_section_register(
        section_id=section.id,
        title=section.title or "",
        zo_mode=section.mode,
    )
    voice_block = format_brand_voice_block(
        brand_voice,
        rfp_client=rfp.client,
        register=register,
    )
    req_block = "\n".join(f"- {r}" for r in requirements) or "- (none mapped)"
    focus_block = (
        "\n".join(f"- {f}" for f in retrieval_focus) if retrieval_focus else "(none)"
    )
    title_cf = (section.title or "").casefold()
    specs_note = ""
    if "5.2.1" in title_cf or (
        "specification" in title_cf and "compliance" in title_cf
    ):
        specs_note = (
            "\nFor Specifications / 5.2.1: answer checklist items in RFP order "
            "(Item 1, Item 2, …) with Meets / Does Not Meet — never substitute "
            "unrelated campaign KPIs for checklist rows.\n"
        )

    system = (
        "You are a senior proposal fact-check editor for zö agency.\n\n"
        "WORK ORDER (mandatory order):\n"
        "1. Read mapped RFP requirements and the RFP excerpt — they define what "
        "this section MUST cover.\n"
        "2. Read the knowledge-base context — verified zö facts, case studies, bios.\n"
        "3. Compare the current draft. If it already satisfies every requirement with "
        "KB-backed or RFP-stated facts (no fabricated eval weights, no false "
        "[VERIFY: insufficient evidence], no wrong checklist answers), return the "
        "same markdown with changed=false.\n"
        "4. NEVER replace real prose with a single [VERIFY: Draft content for … — "
        "insufficient evidence in corpus] stub. Cover letters and executive summaries "
        "may use RFP + plan context without 03_CS case studies.\n"
        "5. If there are gaps, fabrications, or generic boilerplate, rewrite to satisfy "
        "requirements using KB evidence and brand voice. Do not invent a generic section.\n"
        "6. LEGAL ATTESTATIONS: Never assert E-Verify enrollment or 'no conflicts of "
        "interest' as fact. Keep or insert [VERIFY: … Sonja/Operations must confirm]. "
        "For health/coalition RFPs, prefer Recovery Network of Oregon in experience/"
        "references when KB supports it; otherwise FLAG for Sonja. Never invent staffing "
        "hours or a '10-year partnership' credential (founded 2013).\n"
        f"{specs_note}\n"
        f"{ANTI_HALLUCINATION_RULES}\n\n"
        f"{voice_block}\n\n"
        'Return JSON: {"content": "full section markdown", "changed": boolean, '
        '"notes": "one line"}'
    )
    user = (
        f"Section: {section.title} (id={section.id})\n\n"
        f"Mapped requirements:\n{req_block}\n\n"
        f"Retrieval focus:\n{focus_block}\n\n"
        f"RFP excerpt (authoritative for structure, checklists, evaluation):\n"
        f"{rfp_excerpt[:45000]}\n\n"
        f"Knowledge base:\n{kb_context[:45000]}\n\n"
        f"Current draft:\n{(section.content or '')[:14000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=8192,
            temperature=0.0,
        )
        content = str((raw or {}).get("content") or "").strip()
        changed = bool((raw or {}).get("changed"))
        notes = str((raw or {}).get("notes") or "").strip()
        if not content:
            return section, False, notes
        if _reject_destructive_fact_check_rewrite(section.content or "", content):
            logger.warning(
                "KB fact-check refused destructive rewrite for %s (%s)",
                section.id,
                section.title,
            )
            return section, False, "rejected stub downgrade"
        if not changed and content == (section.content or "").strip():
            return section, False, notes
        if content != (section.content or "").strip():
            return section.model_copy(update={"content": content}), True, notes
        return section, False, notes
    except Exception as exc:  # noqa: BLE001
        logger.warning("Requirement-aligned fact-check agent failed for %s: %s", section.id, exc)
        return section, False, ""


def _client_from_section_title(title: str) -> str:
    raw = (title or "").strip()
    if "—" in raw:
        raw = raw.split("—", 1)[1].strip()
    elif " - " in raw:
        raw = raw.split(" - ", 1)[1].strip()
    raw = re.sub(r"^[\d.]+\s*", "", raw).strip()
    return raw[:100]


def _kb_query_for_section(
    section: ProposalSection,
    rfp: RfpRecord,
    mapped: RfpSectionMap | None = None,
) -> str:
    """Bucket-aware Supermemory query — avoid title+client+draft mash (often 0 API hits)."""
    sid = section.id
    title = (section.title or "").strip()
    title_cf = title.casefold()
    client_hint = _client_from_section_title(title)

    if _is_legal_attestation_section(section) or "e-verify" in title_cf:
        return "01_companyfacts zö agency E-Verify enrollment compliance"[:240]
    if "disclosure" in title_cf or "conflict" in title_cf:
        return "01_companyfacts zö agency conflict of interest disclosure"[:240]
    if any(h in title_cf for h in ("reference", "previous experience", "past performance")):
        from app.services.evidence_trust.legal_attestation_gate import (
            rfp_needs_health_coalition_proof,
        )

        if rfp_needs_health_coalition_proof(rfp):
            return (
                "03_CS Recovery Network of Oregon RNO Oregon Recovers coalition"
            )[:240]
        return "03_CS zö agency client references past performance contacts"[:240]

    if mapped and mapped.retrieval_focus:
        focus = " ".join(str(f).strip() for f in mapped.retrieval_focus[:2] if str(f).strip())
        # Avoid vague mash like "methodology won_proposals" as the sole query.
        if focus and not re.search(r"(?i)\bwon_proposals?\b", focus):
            return f"zö agency {focus}"[:240]
        if focus:
            return f"01_companyfacts 03_CS zö agency {focus}"[:240]

    if sid.startswith("section-3-work") or "case study" in title_cf:
        name = client_hint or title
        return f"03_CS {name} case study zö agency outcomes"[:240]

    if sid.startswith("section-2"):
        return f"04_Bio zö agency team {client_hint or title}"[:240]

    if sid.startswith("section-1"):
        if "certification" in title_cf:
            return "01_companyfacts zö agency WBENC WOSB certifications"[:240]
        if "organizational" in title_cf or "structure" in title_cf:
            return "02_MasterTemplate org structure zö agency team roster"[:240]
        if "business" in title_cf and "information" in title_cf:
            return "01_companyfacts zö agency business information insurance financial stability"[:240]
        if "who we are" in title_cf:
            return "01_companyfacts zö agency company overview differentiators founded 2013"[:240]
        topic = client_hint or title
        return f"01_companyfacts zö agency {topic}"[:240]

    terms = _question_terms(client_hint or title)
    core = " ".join(terms[:8])
    return f"01_companyfacts 03_CS zö agency {core}"[:240]


async def _kb_blob_for_section(
    section: ProposalSection,
    rfp: RfpRecord,
    mapped: RfpSectionMap | None = None,
) -> tuple[str, list[str]]:
    query = _kb_query_for_section(section, rfp, mapped=mapped)
    context, sources, _ = await retrieve_for_question(
        query,
        limit=8,
        max_chars=45_000,
        threshold=0.32,
    )
    if context.startswith("(No matching"):
        return "", []
    return context, sources


async def _repair_false_verify_stub(
    section: ProposalSection,
    kb_context: str,
    rfp_excerpt: str,
) -> tuple[ProposalSection, bool]:
    body = section.content or ""
    if not _INSUFFICIENT_EVIDENCE_RE.search(body) and not _FALSE_VERIFY_STUB_RE.search(body):
        return section, False
    if not kb_context.strip() or not llm.is_configured():
        return section, False
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You replace false [VERIFY: insufficient evidence] stubs with KB-backed prose.\n"
                        "Use ONLY facts in the knowledge-base context. Prefer 07_FIN/06_WON *Proposal* "
                        "and 03_CS case studies over client RFP PDFs.\n"
                        "If KB lacks a required fact, keep a specific [VERIFY: field — reason] tag.\n"
                        "Do not invent metrics, clients, or certifications.\n"
                        'Return JSON: {"content": "full section markdown"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Section: {section.title}\n\n"
                        f"Knowledge base:\n{kb_context[:40000]}\n\n"
                        f"RFP excerpt (structure only):\n{rfp_excerpt[:15000]}\n\n"
                        f"Current stub section:\n{body[:8000]}"
                    ),
                },
            ],
            max_tokens=8192,
            temperature=0.0,
        )
        content = str((raw or {}).get("content") or "").strip()
        if content and not _FALSE_VERIFY_STUB_RE.search(content):
            return section.model_copy(update={"content": content}), True
    except Exception as exc:  # noqa: BLE001
        logger.warning("VERIFY stub repair failed for %s: %s", section.id, exc)
    return section, False


def _section3_client_key(section: ProposalSection) -> str:
    raw = (section.title or "").strip()
    if "—" in raw:
        raw = raw.split("—", 1)[1].strip()
    elif " - " in raw:
        raw = raw.split(" - ", 1)[1].strip()
    raw = re.sub(r"^[\d.]+\s*", "", raw).strip()
    tokens = [t for t in re.split(r"\W+", raw.casefold()) if len(t) >= 4]
    generic = {
        "city",
        "county",
        "state",
        "digital",
        "campaign",
        "department",
        "library",
        "rock",
        "locks",
        "case",
        "study",
    }
    distinctive = [t for t in tokens if t not in generic and len(t) >= 5]
    if distinctive:
        return distinctive[0]
    return tokens[0] if tokens else section.id


def _dedupe_section3_case_studies(
    sections: list[ProposalSection],
) -> tuple[list[ProposalSection], int]:
    """Keep one Section 3 card per client — prefer longer body."""
    non_s3: list[ProposalSection] = []
    s3: list[ProposalSection] = []
    for section in sections:
        if section.id.startswith("section-3-work-") and not section.id.endswith("placeholder"):
            s3.append(section)
        else:
            non_s3.append(section)

    by_client: dict[str, ProposalSection] = {}
    anon: list[ProposalSection] = []
    removed = 0
    for section in s3:
        key = _section3_client_key(section)
        if not key:
            anon.append(section)
            continue
        prev = by_client.get(key)
        if prev is None:
            by_client[key] = section
            continue
        if word_count(section.content or "") > word_count(prev.content or ""):
            by_client[key] = section
        removed += 1
        logger.info(
            "Fact-check deduped Section 3 duplicate client %r — kept %s",
            key,
            by_client[key].id,
        )

    return [*non_s3, *by_client.values(), *anon], removed


async def _flag_unverified_metrics_in_case_study(
    section: ProposalSection,
    kb_context: str,
) -> tuple[ProposalSection, bool]:
    """If section cites % not present in KB context, strip or VERIFY via LLM."""
    body = section.content or ""
    if not section.id.startswith("section-3-work-") or not _PERCENT_RE.search(body):
        return section, False
    kb_cf = kb_context.casefold()
    suspicious: list[str] = []
    for match in _PERCENT_RE.finditer(body):
        pct = match.group(0)
        if pct.casefold() not in kb_cf and match.group(1) not in kb_cf:
            suspicious.append(pct)
    if not suspicious or not llm.is_configured():
        return section, False
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Remove or replace unverified statistics in a case study section.\n"
                        "Metrics must appear verbatim in the KB context or become "
                        "[VERIFY: metric — confirm from 03_CS source].\n"
                        'Return JSON: {"content": "..."}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Unverified percentages: {', '.join(suspicious)}\n\n"
                        f"KB:\n{kb_context[:25000]}\n\n"
                        f"Section:\n{body[:8000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.0,
        )
        content = str((raw or {}).get("content") or "").strip()
        if content and content != body:
            return section.model_copy(update={"content": content}), True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Case study metric check failed for %s: %s", section.id, exc)
    return section, False


async def _fact_check_one_section(
    section: ProposalSection,
    *,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    brand_voice: dict[str, Any] | None,
) -> tuple[ProposalSection, FactCheckReport]:
    """Fact-check a single section; returns section + per-section report deltas."""
    report = FactCheckReport(sections_checked=1)
    current = section
    mapped = _resolve_mapped_section(section, research)
    requirements = _requirements_for_section(section, mapped)
    retrieval_focus = list(mapped.retrieval_focus) if mapped else []
    rfp_excerpt = _rfp_excerpt_for_section(
        rfp_context,
        section_title=section.title or "",
        requirements=requirements,
    )
    if not rfp_excerpt.strip():
        rfp_excerpt = (rfp_context or "")[:35_000]

    bad_pcts = _eval_percent_claimed_without_rfp(current.content or "", rfp_context)
    if bad_pcts:
        current, fixed = await _repair_eval_percentages(current, rfp_context)
        if fixed:
            report.eval_repairs += 1
            report.logs.append(
                f"Repaired unsupported evaluation weights ({', '.join(bad_pcts)}) "
                f"in {section.title}"
            )

    if current.id.startswith("section-2-bio"):
        current, bio_fills, bio_logs = await _fact_check_bio_subsections(current)
        if bio_fills:
            report.verify_tags_filled += bio_fills
        report.logs.extend(bio_logs)

    # Legal attestations: deterministic gate only — do not LLM-rewrite into sworn claims.
    if _is_legal_attestation_section(current):
        from app.services.evidence_trust.legal_attestation_gate import (
            gate_section_legal_attestations,
        )

        current, legal = gate_section_legal_attestations(current, force=True)
        report.logs.extend(legal.logs)
        priority = _priority_kb_queries(
            current, rfp=rfp, rfp_context=rfp_context
        ) or [_kb_query_for_section(current, rfp, mapped=mapped)]
        kb_context, sources = await _gather_kb_context(
            priority,
            fallback_section=current,
            rfp=rfp,
            mapped=mapped,
        )
        if kb_context:
            new_body, fills = _replace_verify_tags_from_blob(
                current.content or "",
                kb_context,
            )
            if fills:
                report.verify_tags_filled += fills
                current = current.model_copy(update={"content": new_body})
                report.logs.append(
                    f"Filled {fills} non-locked VERIFY tag(s) in {section.title} "
                    f"from KB ({', '.join(sources[:3])})"
                )
        return current, report

    kb_context = ""
    sources: list[str] = []

    if _should_run_requirement_agent(current, mapped, current.content or ""):
        queries = await _plan_kb_queries(
            section=current,
            requirements=requirements,
            retrieval_focus=retrieval_focus,
            rfp=rfp,
            mapped=mapped,
            rfp_context=rfp_context,
        )
        kb_context, sources = await _gather_kb_context(
            queries,
            fallback_section=current,
            rfp=rfp,
            mapped=mapped,
        )
        if _is_whole_section_draft_stub(current.content or "") and not kb_context.strip():
            logger.info(
                "KB fact-check: skipping stub repair for %s — no KB evidence found",
                section.id,
            )
            return current, report
        current, agent_fixed, notes = await _run_requirement_aligned_fact_check_agent(
            current,
            requirements=requirements,
            retrieval_focus=retrieval_focus,
            rfp_excerpt=rfp_excerpt,
            kb_context=kb_context,
            rfp=rfp,
            brand_voice=brand_voice,
        )
        if agent_fixed:
            report.requirement_repairs += 1
            detail = notes or "Requirement-aligned KB rewrite"
            report.logs.append(
                f"Smart fact-check ({detail}) in {section.title} "
                f"[KB: {', '.join(sources[:3]) or 'n/a'}]"
            )
            if _INSUFFICIENT_EVIDENCE_RE.search(current.content or ""):
                report.stubs_repaired += 1
    else:
        # Still inject priority queries (RNO / hours / founded) even on light path.
        priority = _priority_kb_queries(current, rfp=rfp, rfp_context=rfp_context)
        if priority:
            kb_context, sources = await _gather_kb_context(
                priority,
                fallback_section=current,
                rfp=rfp,
                mapped=mapped,
            )
        else:
            kb_context, sources = await _kb_blob_for_section(
                current, rfp, mapped=mapped
            )

    if kb_context:
        new_body, fills = _replace_verify_tags_from_blob(
            current.content or "",
            kb_context,
        )
        if fills:
            report.verify_tags_filled += fills
            current = current.model_copy(update={"content": new_body})
            report.logs.append(
                f"Filled {fills} VERIFY tag(s) in {section.title} "
                f"from KB ({', '.join(sources[:3])})"
            )

        if _INSUFFICIENT_EVIDENCE_RE.search(current.content or ""):
            current, stub_fixed = await _repair_false_verify_stub(
                current,
                kb_context,
                rfp_excerpt,
            )
            if stub_fixed:
                report.stubs_repaired += 1
                report.logs.append(
                    f"Replaced false insufficient-evidence stub in {section.title}"
                )
    elif _INSUFFICIENT_EVIDENCE_RE.search(current.content or ""):
        kb_context, sources = await _kb_blob_for_section(current, rfp, mapped=mapped)
        if kb_context:
            current, stub_fixed = await _repair_false_verify_stub(
                current,
                kb_context,
                rfp_excerpt,
            )
            if stub_fixed:
                report.stubs_repaired += 1
                report.logs.append(
                    f"Replaced false insufficient-evidence stub in {section.title}"
                )

    if current.id.startswith("section-3-work-"):
        if not kb_context:
            kb_context, _ = await _kb_blob_for_section(current, rfp, mapped=mapped)
        if kb_context:
            current, metric_fixed = await _flag_unverified_metrics_in_case_study(
                current,
                kb_context,
            )
            if metric_fixed:
                report.metric_flags += 1
                report.logs.append(f"Scrubbed unverified metrics in {section.title}")

    # Always scrub hours / decade filler even outside attestation titles.
    from app.services.evidence_trust.legal_attestation_gate import (
        gate_section_legal_attestations,
    )

    current, filler = gate_section_legal_attestations(current)
    report.logs.extend(filler.logs)

    return current, report


async def run_kb_fact_check_pass(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None = None,
    only_section_ids: list[str] | None = None,
) -> tuple[ProposalDraft, FactCheckReport]:
    """Cross-verify sections; repair from KB when evidence exists.

    When ``only_section_ids`` is set, only those sections are fact-checked; others pass through unchanged.
    """
    only_set = set(only_section_ids) if only_section_ids else None
    report = FactCheckReport()
    brand_voice = _brand_voice_payload(research)
    scope = (
        f"{len(only_set)} targeted"
        if only_set is not None
        else str(len(draft.sections))
    )
    logger.info(
        "KB fact-check pass for %s — %s section(s), mapped_rfp_sections=%d, "
        "section_parallel=%d",
        draft.rfp_id,
        scope,
        len(research.rfp_sections) if research and research.rfp_sections else 0,
        FACT_CHECK_SECTION_PARALLEL,
    )

    # Preserve original order; process eligible sections concurrently.
    indexed = list(enumerate(draft.sections))
    results: list[ProposalSection | None] = [None] * len(draft.sections)
    work: list[tuple[int, ProposalSection]] = []
    for idx, section in indexed:
        if only_set is not None and section.id not in only_set:
            results[idx] = section
            continue
        work.append((idx, section))

    sem = asyncio.Semaphore(FACT_CHECK_SECTION_PARALLEL)

    async def _run(idx: int, section: ProposalSection) -> tuple[int, ProposalSection, FactCheckReport]:
        async with sem:
            updated, partial = await _fact_check_one_section(
                section,
                rfp=rfp,
                rfp_context=rfp_context,
                research=research,
                brand_voice=brand_voice,
            )
            return idx, updated, partial

    if work:
        gathered = await asyncio.gather(*[_run(i, s) for i, s in work])
        for idx, updated, partial in gathered:
            results[idx] = updated
            report.sections_checked += partial.sections_checked
            report.verify_tags_filled += partial.verify_tags_filled
            report.stubs_repaired += partial.stubs_repaired
            report.eval_repairs += partial.eval_repairs
            report.requirement_repairs += partial.requirement_repairs
            report.metric_flags += partial.metric_flags
            report.logs.extend(partial.logs)

    updated_sections = [s for s in results if s is not None]
    if len(updated_sections) != len(draft.sections):
        # Safety: never drop sections if a race left a hole.
        updated_sections = [
            results[i] if results[i] is not None else draft.sections[i]
            for i in range(len(draft.sections))
        ]

    run_s3_dedupe = only_set is None or any(
        sid.startswith("section-3-work-") for sid in only_set
    )
    if run_s3_dedupe:
        updated_sections, dupes = _dedupe_section3_case_studies(updated_sections)
    else:
        dupes = 0
    report.duplicates_removed = dupes
    if dupes:
        report.logs.append(f"Removed {dupes} duplicate Section 3 case study card(s)")

    draft = draft.model_copy(update={"sections": updated_sections})

    # Manuscript-level legal / RNO gate after parallel section work.
    from app.services.evidence_trust.legal_attestation_gate import (
        apply_legal_attestation_gates,
    )

    draft, legal_report = apply_legal_attestation_gates(
        draft,
        rfp=rfp,
        rfp_context=rfp_context,
    )
    if legal_report.logs:
        report.logs.extend(legal_report.logs)
        logger.info(
            "Legal attestation gate for %s: everify=%d conflicts=%d hours=%d filler=%d rno=%d",
            draft.rfp_id,
            legal_report.everify_flags,
            legal_report.conflict_flags,
            legal_report.hours_flags,
            legal_report.filler_flags,
            legal_report.rno_flags,
        )

    if report.logs:
        logger.info(
            "KB fact-check for %s: %d sections, verify_fills=%d stubs=%d eval=%d req=%d dupes=%d",
            draft.rfp_id,
            report.sections_checked,
            report.verify_tags_filled,
            report.stubs_repaired,
            report.eval_repairs,
            report.requirement_repairs,
            report.duplicates_removed,
        )

    return draft, report


async def run_kb_fact_check_section_ids(
    draft: ProposalDraft,
    section_ids: list[str],
    *,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, FactCheckReport]:
    """Fact-check only the given section ids (incremental generation hook)."""
    ids = [sid for sid in section_ids if sid and sid.strip()]
    if not ids:
        return draft, FactCheckReport()
    return await run_kb_fact_check_pass(
        draft,
        rfp=rfp,
        rfp_context=rfp_context,
        research=research,
        only_section_ids=ids,
    )


async def run_kb_fact_check_for_rfp(rfp_id: str) -> tuple[ProposalDraft, FactCheckReport]:
    from app.services.proposal_common import aload_rfp_for_proposal
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft

    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        from app.services.proposal_common import ProposalError

        raise ProposalError("No proposal draft for fact-check.", status_code=400)
    from app.services.proposal_repository import aget_research_cache

    rfp, _, rfp_context = await aload_rfp_for_proposal(rfp_id)
    research = await aget_research_cache(rfp_id)
    draft, report = await run_kb_fact_check_pass(
        draft,
        rfp=rfp,
        rfp_context=rfp_context,
        research=research,
    )
    await asave_proposal_draft(draft)
    return draft, report
