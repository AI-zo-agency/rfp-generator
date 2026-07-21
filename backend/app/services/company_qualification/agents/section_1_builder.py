"""Section 1 Builder — assembles human-readable Section 1 prose from the plan."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.services import llm
from app.services.agency_facts import agency_tenure_block, enforce_agency_tenure
from app.services.company_qualification.schemas import (
    CompanyTruth,
    GeneratedSubsection,
    PrioritizedCapabilities,
    ProposalContext,
    Section1CompositionResult,
    Section1PlanResult,
)

logger = logging.getLogger(__name__)

SECTION_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "section-1-who-we-are",
        "1.1 — Who We Are",
        "Structure: opening brand paragraphs (no Who We Are heading), then blank line, "
        "then '## Our Promise' on its own line. Our Promise = warm commitment tone only — "
        "NO staff names, titles, channels, CRM, dashboards, or report cadences. "
        "Client bridge max 1–2 feeling sentences. Never inline headings. No whole-paragraph bold. "
        "Lead with zö brand essence (zö = family/kindred/clan; strongest advocate). "
        "HARD MAX 250 words (prefer ~200). "
        "Use canonical agency years from CompanyTruth / tenure block — never invent a different year count. "
        "No certifications or insurance. Address ONLY the RFP client named in the prompt.",
    ),
    (
        "section-1-org-structure",
        "1.2 — Organizational Structure",
        "List the COMPLETE organizational structure from the Master Team Roster.\n"
        "Group by department: Leadership → Client Services → Project Management → Creative → "
        "Finance/HR → Coaches/Implementors (or whatever departments the roster uses).\n"
        "For EVERY person: **Name** — Title. Do NOT omit anyone named in the roster.\n"
        "Do NOT invent people. Do NOT write full resumes or bios.\n"
        "Open with 1-2 sentences that we are organized to deliver projects efficiently, "
        "then the full department lists.",
    ),
    (
        "section-1-business-info",
        "1.3 — Business Information",
        "Facts/table only: legal name, DBA, ownership, EIN, registration, addresses, contact. "
        "Founded August 21, 2013; Years in Operation must match canonical tenure (same number as Who We Are). "
        "NO narrative, NO Who We Are copy, NO certifications, NO insurance.",
    ),
    (
        "section-1-certifications",
        "1.4 — Certifications",
        "Filter certifications relevant to RFP context. Agency certs only (WBENC, WOSB). "
        "No platform/individual certs.",
    ),
    (
        "section-1-insurance",
        "1.5 — Insurance Information",
        "Coverage types from company truth. Use [VERIFY: amount] for unknown dollar figures.",
    ),
)

SubsectionProgressCallback = Callable[[GeneratedSubsection], Awaitable[None]]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _budget_hint(plan: Section1PlanResult, section_id: str) -> str:
    for b in plan.content_budget.budgets:
        if b.section_id == section_id:
            bits = [f"format={b.format}"]
            if b.word_min is not None:
                bits.append(f"min={b.word_min}")
            if b.word_max is not None:
                bits.append(f"max={b.word_max}")
            if b.notes:
                bits.append(b.notes)
            return ", ".join(bits)
    return ""


async def _write_one_subsection(
    *,
    sec_id: str,
    title: str,
    hint: str,
    section1_plan: Section1PlanResult,
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
    primary_caps: list[str],
    omit_caps: list[str],
    brand_voice_block: str,
    rfp_client: str,
    rfp_sector: str,
    master_roster_excerpt: str,
) -> tuple[GeneratedSubsection, str]:
    sub_plan = section1_plan.section_plan.get(sec_id)
    plan_json = sub_plan.model_dump_json() if sub_plan else "{}"
    budget = _budget_hint(section1_plan, sec_id)
    roster_block = (
        f"Master Team Roster — INCLUDE EVERY PERSON BELOW (name + title), grouped by department:\n"
        f"{master_roster_excerpt[:200000]}\n"
        if master_roster_excerpt and sec_id == "section-1-org-structure"
        else ""
    )

    max_tokens = 8192 if sec_id == "section-1-org-structure" else 3072
    temperature = 0.55 if sec_id == "section-1-who-we-are" else 0.2
    tenure = (
        agency_tenure_block() + "\n\n"
        if sec_id in {"section-1-who-we-are", "section-1-business-info"}
        else ""
    )

    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Section 1 Builder for zö agency.\n"
                        f"Write ONLY the subsection: {title} ({sec_id}).\n"
                        "ASSEMBLE from the approved plan and CompanyTruth. Do NOT invent facts.\n"
                        "Apply the content budget. Omit-tier capabilities must not appear.\n"
                        "Write in first person (we/our/us). Never use 'The Vendor'.\n"
                        "Return ONE complete JSON object — no markdown fences. Finish every string/brace.\n"
                        + (
                            "CRITICAL for Org Structure: list EVERY person from the Master Team Roster "
                            "with their exact title. Incomplete org charts are failures.\n"
                            if sec_id == "section-1-org-structure"
                            else ""
                        )
                        + (
                            "Write with bold zö brand voice — warm, human, attractive to the client. "
                            "## Our Promise on its own line as a vow. "
                            "FORBIDDEN in 1.1: staff names, titles, SEM/SEO/PPC lists, CRM, dashboards, report SLAs.\n"
                            if sec_id == "section-1-who-we-are"
                            else ""
                        )
                        + "\nReturn JSON:\n"
                        f'{{"id": "{sec_id}", "title": "{title}", "content": "markdown", "wordCount": 0}}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Voice:\n{brand_voice_block}\n\n"
                        f"{tenure}"
                        f"Client: {rfp_client} | Sector: {rfp_sector}\n"
                        f"Budget: {budget}\n"
                        f"Spec: {hint}\n\n"
                        f"Subsection plan:\n{plan_json}\n\n"
                        f"CompanyTruth:\n{company_truth.model_dump_json()}\n\n"
                        f"ProposalContext:\n{proposal_context.model_dump_json()}\n\n"
                        f"Primary capabilities: {primary_caps}\n"
                        f"Omit capabilities (must NOT appear): {omit_caps}\n\n"
                        f"{roster_block}"
                    ),
                },
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            tier="heavy" if sec_id in {"section-1-who-we-are", "section-1-org-structure"} else "light",
        )
    except Exception as exc:  # noqa: BLE001 — never abort remaining subsections
        logger.warning(
            "Section 1 Builder %s failed (%s); VERIFY stub (no retry)",
            sec_id,
            str(exc)[:180],
        )
        stub = (
            f"[VERIFY: complete {title} from CompanyTruth / Master Team Roster — "
            f"generation interrupted mid-JSON]"
        )
        return (
            GeneratedSubsection(
                id=sec_id,
                title=title,
                content=stub,
                wordCount=_word_count(stub),
            ),
            "failed",
        )

    try:
        sec = GeneratedSubsection.model_validate(raw)
    except Exception:
        content = str(raw.get("content") or "").strip()
        sec = GeneratedSubsection(id=sec_id, title=title, content=content)
    if sec.id != sec_id:
        sec = sec.model_copy(update={"id": sec_id, "title": title})
    content = sec.content or ""
    if sec_id in {"section-1-who-we-are", "section-1-business-info"}:
        content = enforce_agency_tenure(content)
    return sec.model_copy(update={"content": content, "wordCount": _word_count(content)}), provider


async def run_section_1_builder_agent(
    *,
    section1_plan: Section1PlanResult,
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
    prioritized_capabilities: PrioritizedCapabilities,
    brand_voice_block: str,
    rfp_client: str,
    rfp_sector: str,
    master_roster_excerpt: str = "",
    on_subsection: SubsectionProgressCallback | None = None,
) -> tuple[Section1CompositionResult, str]:
    """Write Section 1 subsections one at a time so each can appear in the UI immediately."""
    primary_caps = [c.capability for c in prioritized_capabilities.primary]
    omit_caps = [c.capability for c in prioritized_capabilities.omit]

    fixed_sections: list[GeneratedSubsection] = []
    provider = ""

    for sec_id, title, hint in SECTION_SPECS:
        logger.info("  └─ [Section 1 Builder] writing %s", title)
        sec, provider = await _write_one_subsection(
            sec_id=sec_id,
            title=title,
            hint=hint,
            section1_plan=section1_plan,
            company_truth=company_truth,
            proposal_context=proposal_context,
            primary_caps=primary_caps,
            omit_caps=omit_caps,
            brand_voice_block=brand_voice_block,
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            master_roster_excerpt=master_roster_excerpt,
        )
        fixed_sections.append(sec)
        if on_subsection:
            await on_subsection(sec)

    result = Section1CompositionResult(
        sectionPlan=dict(section1_plan.section_plan),
        generatedSections=fixed_sections,
    )
    return result, provider
