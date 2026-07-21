"""Manuscript locks — single source of truth for cross-section commitments.

Locks primary account contact + RFQ-named KPIs early (Phase 2), injects them into
every drafting/self-edit pass, and fails consistency when sections conflict or omit them.

No hand-rolled name/KPI regexes — scanning uses fixed phrase lists + LLM when needed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import (
    ManuscriptLocks,
    PreSubmitIssue,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services import llm

logger = logging.getLogger(__name__)

# Fixed role phrases — a section "claims" a primary if one of these appears with a person name.
_PRIMARY_ROLE_PHRASES: tuple[str, ...] = (
    "primary account representative",
    "primary account rep",
    "primary liaison",
    "primary contact",
    "dedicated customer service representative",
    "dedicated representative",
    "day-to-day account",
    "day to day account",
    "single point of contact",
    "named account manager",
    "named account lead",
    "named account representative",
)

# People who might be incorrectly named as primary (checked as plain substrings).
_ACCOUNT_CANDIDATE_NAMES: tuple[str, ...] = (
    "Ron Comer",
    "Haley Neff",
    "Sonja Anderson",
    "Sonja M. Anderson",
    "Todd Anderson",
    "Ella Lindau",
)

_REPORTING_TITLE_MARKERS: tuple[str, ...] = (
    "methodolog",
    "report",
    "analytics",
    "optimiz",
    "measurement",
    "kpi",
    "metric",
    "evaluat",
)

_META_MARKERS: tuple[str, ...] = (
    "not present",
    "knowledge base",
    "case study master",
    "requested file",
    "pull additional metrics",
    "before final submission",
    "standalone verified",
    "creative examples:",
)

_NOTE_PREFIXES: tuple[str, ...] = (
    "note:",
    "*note",
    "**note",
    "internal note",
    "kb note",
    "editor note",
    "creative examples:",
    "*creative examples",
)


def format_manuscript_locks_block(locks: ManuscriptLocks | None) -> str:
    if not locks or not locks.primary_contact_name:
        return ""
    kpi_lines = "\n".join(f"  - {k}" for k in locks.required_kpis) or "  - (none extracted)"
    human = (
        "\n⚠ needsHumanConfirm=true — Sonja may override primary contact; "
        "until then use the locked name everywhere."
        if locks.needs_human_confirm
        else ""
    )
    return (
        "## MANUSCRIPT LOCKS (mandatory — never contradict across sections)\n"
        f"- Primary contact (ONE person for dedicated account / primary liaison / "
        f"day-to-day POC): **{locks.primary_contact_name}**"
        f"{f' — {locks.primary_contact_title}' if locks.primary_contact_title else ''}\n"
        f"- Role label: {locks.primary_contact_role or 'Primary Account Representative / liaison'}\n"
        + (
            f"- Executive sponsor (escalation only — NOT day-to-day primary): "
            f"**{locks.executive_sponsor_name}**\n"
            if locks.executive_sponsor_name
            else ""
        )
        + f"- RFQ-named KPIs / brand-awareness metrics that MUST appear in Methodology "
        f"and Reporting (exact concepts, not paraphrased away):\n{kpi_lines}\n"
        + (f"- Lock rationale: {locks.decision_rationale}\n" if locks.decision_rationale else "")
        + human
        + "\nRULES:\n"
        "1. Never name a different person as primary liaison / dedicated account rep.\n"
        "2. Team bios header for the account lead must match the locked primary contact.\n"
        "3. Every RFQ-named KPI above must appear at least once in reporting/methodology tabs.\n"
        "4. Do not invent extra primary contacts.\n"
    )


async def build_manuscript_locks(
    *,
    rfp: RfpRecord,
    rfp_context: str,
    plan: Any | None = None,
    roster_excerpt: str = "",
) -> ManuscriptLocks:
    """LLM: lock one primary contact + extract exact RFQ-named KPIs."""
    plan_bits = ""
    if plan is not None:
        try:
            opp = getattr(plan, "opportunity", None)
            delivery = getattr(plan, "delivery", None)
            if opp is not None:
                success = getattr(opp, "success_criteria", None)
                if success is not None and hasattr(success, "model_dump_json"):
                    plan_bits += f"Success criteria:\n{success.model_dump_json()}\n"
                understanding = getattr(opp, "understanding", None)
                if understanding is not None and hasattr(understanding, "model_dump_json"):
                    plan_bits += f"Understanding:\n{understanding.model_dump_json()[:4000]}\n"
            if delivery is not None:
                resources = getattr(delivery, "resources", None)
                if resources is not None and hasattr(resources, "model_dump_json"):
                    plan_bits += f"Resources:\n{resources.model_dump_json()}\n"
                delivery_model = getattr(delivery, "delivery_model", None)
                if delivery_model is not None and hasattr(delivery_model, "model_dump_json"):
                    plan_bits += f"Delivery model:\n{delivery_model.model_dump_json()}\n"
        except Exception as exc:  # noqa: BLE001
            logger.debug("plan excerpt for locks failed: %s", exc)

    now = datetime.now(timezone.utc).isoformat()
    fallback = ManuscriptLocks(
        primaryContactName="Ron Comer",
        primaryContactTitle="Senior Account Manager",
        primaryContactRole="primary liaison / dedicated account representative",
        executiveSponsorName="Todd Anderson",
        requiredKpis=[],
        decisionRationale="Fallback lock — LLM parse failed; prefer SAM for day-to-day account work.",
        needsHumanConfirm=True,
        updatedAt=now,
    )

    try:
        raw, _provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You lock cross-section manuscript facts for zö agency proposals.\n"
                        "Return compact JSON only (no markdown fences, no commentary):\n"
                        "{\n"
                        '  "primaryContactName": "Full Name",\n'
                        '  "primaryContactTitle": "Title from roster",\n'
                        '  "primaryContactRole": "dedicated customer service representative / primary liaison",\n'
                        '  "executiveSponsorName": "Name or empty",\n'
                        '  "requiredKpis": ["exact RFQ-named metric phrases"],\n'
                        '  "decisionRationale": "1-2 sentences",\n'
                        '  "needsHumanConfirm": false\n'
                        "}\n\n"
                        "PRIMARY CONTACT RULES:\n"
                        "- Lock EXACTLY one day-to-day primary account contact.\n"
                        "- For multi-year media buying / retainers / high-touch account management RFQs: "
                        "prefer Senior Account Manager (e.g. Ron Comer) over Agency Director/CEO.\n"
                        "- For small municipal brand-strategy engagements: Agency Director (Sonja) may be primary.\n"
                        "- Executive sponsor is escalation only — never the same as day-to-day primary unless "
                        "the RFQ is tiny and clearly director-led.\n"
                        "- Only pick names that appear in the roster excerpt when provided.\n"
                        "- Set needsHumanConfirm true when the call is ambiguous (state agency + CEO vs SAM).\n\n"
                        "KPI RULES:\n"
                        "- Extract EVERY named brand-awareness / reporting metric the RFQ itself names "
                        "(e.g. specific media property viewership/subscriptions, survey names).\n"
                        "- Prefer exact RFQ phrasing. Do not invent KPIs not in the RFQ.\n"
                        "- Include metrics from Attachment/scoring sections about reporting & analytics.\n"
                        "- Keep requiredKpis short phrases; do not write long essays in any field.\n"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Client: {rfp.client}\nTitle: {rfp.title}\nSector: {rfp.sector}\n\n"
                        f"{plan_bits}\n"
                        f"Roster excerpt:\n{roster_excerpt[:20000] or '(not provided — use known zö account leads)'}\n\n"
                        f"RFP excerpt:\n{rfp_context[:45000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.0,
            tier="light",
        )
    except Exception as exc:  # noqa: BLE001 — locks must never abort Sections 1–3
        logger.warning("manuscript locks LLM failed: %s — using fallback", exc)
        return fallback

    try:
        locks = ManuscriptLocks.model_validate(
            {
                **(raw or {}),
                "updatedAt": now,
            }
        )
    except Exception as exc:
        logger.warning("manuscript locks validation failed: %s — using fallback", exc)
        locks = fallback

    if not locks.primary_contact_name.strip():
        locks = locks.model_copy(
            update={
                "primary_contact_name": "Ron Comer",
                "primary_contact_title": locks.primary_contact_title or "Senior Account Manager",
                "needs_human_confirm": True,
            }
        )

    kpis = [str(k).strip() for k in locks.required_kpis if str(k).strip()]
    locks = locks.model_copy(update={"required_kpis": kpis})
    logger.info(
        "Manuscript locks for %s: primary=%s kpis=%d humanConfirm=%s",
        rfp.id,
        locks.primary_contact_name,
        len(locks.required_kpis),
        locks.needs_human_confirm,
    )
    return locks


def _norm_person(name: str) -> str:
    return " ".join(name.strip().split())


def _name_variants(name: str) -> list[str]:
    """Plain string variants — no regex (e.g. Sonja Anderson / Sonja M. Anderson)."""
    base = _norm_person(name)
    variants = [base]
    parts = base.split()
    if len(parts) >= 3 and len(parts[1]) <= 2:
        # Drop middle initial: "Sonja M. Anderson" → "Sonja Anderson"
        variants.append(f"{parts[0]} {parts[-1]}")
    elif len(parts) == 2:
        variants.append(f"{parts[0]} M. {parts[1]}")
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.casefold()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _section_has_primary_role_claim(content: str) -> bool:
    lower = content.casefold()
    return any(phrase in lower for phrase in _PRIMARY_ROLE_PHRASES)


def _candidate_names_for_scan(locks: ManuscriptLocks) -> list[str]:
    names = list(_ACCOUNT_CANDIDATE_NAMES)
    for extra in (locks.primary_contact_name, locks.executive_sponsor_name):
        if extra and extra.strip():
            names.extend(_name_variants(extra))
    # Dedupe by casefold
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.casefold()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _people_claimed_as_primary(content: str, candidates: list[str]) -> list[str]:
    """Section-level co-occurrence: primary role phrase + person name (plain substrings)."""
    if not content.strip() or not _section_has_primary_role_claim(content):
        return []
    lower = content.casefold()
    found: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        if name.casefold() in lower and name.casefold() not in seen:
            seen.add(name.casefold())
            found.append(_norm_person(name))
    return found


def _is_reporting_title(title: str) -> bool:
    lower = title.casefold()
    return any(marker in lower for marker in _REPORTING_TITLE_MARKERS)


def _kpi_present(kpi: str, haystack: str) -> bool:
    """True when the KPI phrase (or its distinctive words) appears — plain strings only."""
    kpi_l = kpi.casefold().strip()
    if len(kpi_l) < 8:
        return True
    if kpi_l in haystack:
        return True
    # Split on spaces / common punctuation without regex
    for ch in (",", "/", "-", "(", ")", ".", ":", ";"):
        kpi_l = kpi_l.replace(ch, " ")
    tokens = [
        t
        for t in kpi_l.split()
        if len(t) >= 4 and t not in {"with", "from", "that", "this", "and", "the"}
    ]
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in haystack)
    need = max(2, (len(tokens) + 1) // 2)
    return hits >= need


def scan_manuscript_lock_issues(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[PreSubmitIssue]:
    """Critical: conflicting primary contacts or missing RFQ-named KPIs."""
    locks = research.manuscript_locks if research else None
    if not locks or not locks.primary_contact_name:
        return []

    issues: list[PreSubmitIssue] = []
    locked = _norm_person(locks.primary_contact_name)
    locked_keys = {v.casefold() for v in _name_variants(locked)}
    candidates = _candidate_names_for_scan(locks)

    claimed_by_section: list[tuple[ProposalSection, list[str]]] = []
    all_claimed: list[str] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue
        names = _people_claimed_as_primary(content, candidates)
        if names:
            claimed_by_section.append((section, names))
            all_claimed.extend(names)

    others = sorted(
        {n for n in all_claimed if n.casefold() not in locked_keys},
        key=str.casefold,
    )
    if others:
        for section, names in claimed_by_section:
            bad = [n for n in names if n.casefold() not in locked_keys]
            if not bad:
                continue
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="manuscript_locks",
                    message=(
                        f"Primary contact lock is '{locked}', but this section names "
                        f"{', '.join(bad)} as primary/liaison/dedicated rep. "
                        f"Use only {locked} everywhere"
                        + (
                            f" (also conflicted with: {', '.join(others)})"
                            if len(others) > 1
                            else ""
                        )
                        + "."
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )

    corpus = "\n".join(
        f"{s.title}\n{s.content}" for s in draft.sections if (s.content or "").strip()
    ).casefold()

    reporting_sections = [
        s
        for s in draft.sections
        if (s.content or "").strip() and _is_reporting_title(s.title)
    ]
    reporting_blob = "\n".join(
        f"{s.title}\n{s.content}" for s in reporting_sections
    ).casefold()

    for kpi in locks.required_kpis:
        haystack = reporting_blob if reporting_sections else corpus
        if _kpi_present(kpi, haystack):
            continue
        target = reporting_sections[0] if reporting_sections else None
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="manuscript_locks",
                message=(
                    f"RFQ-named KPI missing from Methodology/Reporting: '{kpi}'. "
                    "Add this exact metric to brand-awareness / reporting tracking."
                ),
                sectionId=target.id if target else None,
                sectionTitle=target.title if target else None,
            )
        )

    return issues


async def audit_manuscript_locks_with_llm(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[PreSubmitIssue]:
    """Optional LLM audit — use when phrase scan needs a second opinion."""
    locks = research.manuscript_locks if research else None
    if not locks or not locks.primary_contact_name:
        return []

    excerpts: list[str] = []
    for section in draft.sections:
        content = (section.content or "").strip()
        if not content:
            continue
        excerpts.append(f"### {section.title} [{section.id}]\n{content[:2500]}")
        if len(excerpts) >= 16:
            break

    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You audit proposal manuscript locks. Return JSON only:\n"
                    "{\n"
                    '  "issues": [\n'
                    "    {\n"
                    '      "severity": "critical",\n'
                    '      "sectionId": "string or null",\n'
                    '      "sectionTitle": "string or null",\n'
                    '      "message": "string"\n'
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    "Flag ONLY: (1) someone other than the locked primary named as "
                    "primary liaison / dedicated account rep / day-to-day POC; "
                    "(2) RFQ-named KPIs missing from methodology/reporting sections.\n"
                    "Do not invent issues."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Locked primary: {locks.primary_contact_name} "
                    f"({locks.primary_contact_title})\n"
                    f"Locked KPIs: {locks.required_kpis}\n\n"
                    + "\n\n".join(excerpts)
                ),
            },
        ],
        max_tokens=2048,
        temperature=0.0,
    )

    issues: list[PreSubmitIssue] = []
    for item in (raw or {}).get("issues") or []:
        if not isinstance(item, dict):
            continue
        msg = str(item.get("message") or "").strip()
        if not msg:
            continue
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="manuscript_locks",
                message=msg,
                sectionId=item.get("sectionId"),
                sectionTitle=item.get("sectionTitle"),
            )
        )
    return issues


def strip_leaked_markdown_wrappers(text: str) -> str:
    """Remove LLM/KB leak prefixes (retrieval: + ```markdown fences) from section bodies."""
    if not text:
        return text
    t = text.strip()
    t = re.sub(
        r"^(?:retrieval|context|kb|source|markdown)\s*:\s*\n?",
        "",
        t,
        flags=re.I,
    )
    wrapped = re.match(
        r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$",
        t,
        flags=re.I,
    )
    if wrapped:
        t = wrapped.group(1).strip()
    else:
        t = re.sub(r"^```(?:markdown|md)?\s*\n", "", t, flags=re.I)
        t = re.sub(r"\n```\s*$", "", t)
    # Designer/layout comments from OCR templates — not client-facing copy.
    t = re.sub(r"<!--[\s\S]*?-->", "", t)
    return t.strip()


def strip_internal_proposal_meta(text: str) -> str:
    """Remove KB-missing notes, editor asides, and leaked word-count lines (no regex)."""
    if not text:
        return text

    kept_paragraphs: list[str] = []
    for para in text.split("\n\n"):
        lower = para.casefold().strip()
        stripped_start = lower.lstrip("* ").lstrip()
        is_note = any(stripped_start.startswith(p) for p in _NOTE_PREFIXES)
        has_meta = any(m in lower for m in _META_MARKERS)
        if (is_note and has_meta) or (
            "requested file" in lower and "not present" in lower
        ):
            continue

        kept_lines: list[str] = []
        for line in para.split("\n"):
            ls = line.strip().casefold()
            if not ls:
                kept_lines.append(line)
                continue
            if ls in {"---", "----", "-----"} or set(ls) <= {"-"}:
                continue
            if ls.startswith("word count"):
                continue
            # "336 words" / "Word count: 336"
            if ls.endswith(" words"):
                maybe_num = ls[: -len(" words")].strip().rstrip(":")
                if maybe_num.isdigit():
                    continue
            kept_lines.append(line)

        rebuilt = "\n".join(kept_lines).strip()
        if rebuilt:
            kept_paragraphs.append(rebuilt)

    return "\n\n".join(kept_paragraphs).strip()
