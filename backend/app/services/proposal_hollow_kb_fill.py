"""Agentic missing-answer fill for Generate + Complete Scan final stage.

Whole-proposal mechanical inventory → one planner LLM → few KB queries →
fill only the planned sections. Not a bulk rewrite of every tab.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import llm

logger = logging.getLogger(__name__)

_EMPTY_LABELED_FIELD_RE = re.compile(
    r"(?im)^(?:[-*•]\s*)?(?:\*\*)?("
    r"Qualifications|Relevant projects|Relevant work|Relevant experience|"
    r"Municipal branding experience|Experience|Key accounts|Proof points|"
    r"Past performance|Capabilities|Role|Contact|Phone|Email|Address|"
    r"FEIN|EIN|License|Certification|Reference|Outcome|Metric|Fee|Rate|"
    r"Timeline|Deliverable|Hours|Quantity"
    r")(?:\*\*)?:\s*$"
)

_EMPTY_TABLE_CELL_RE = re.compile(
    r"(?m)^\s*\|(?:\s*\|\s*)+\|\s*$|^\s*\|[^|\n]*\|\s*(?:TBD|N/?A|—|–|-)?\s*\|\s*$",
    re.I,
)

_PLACEHOLDER_VALUE_RE = re.compile(
    r"(?im)^[-*•]\s*(?:\*\*[^*]+\*\*:?\s*)?(?:TBD|TBC|TODO|N/?A|\[?\s*insert\s*\]?)\s*$"
)

_MAX_SECTIONS_PER_PASS = 6
_MAX_PLAN_FILLS = _MAX_SECTIONS_PER_PASS
_MAX_KB_QUERIES = 3
_EVIDENCE_CHARS = 14_000
_FILL_MAX_TOKENS = 4096


@dataclass
class MissingAnswerGap:
    section_id: str
    title: str
    reasons: list[str]
    snippet: str


def _skip_section(section: ProposalSection) -> bool:
    """Skip intentional static cards / bio PDF stubs — not 'missing answers'."""
    sid = section.id or ""
    if sid.startswith("section-1-"):
        return True
    if sid.startswith("section-2-bio-"):
        return True
    if sid.startswith("section-3-"):
        from app.services.proposal_bio_stub import looks_like_bio_stub_body
        from app.services.proposal_case_study_stub import (
            looks_like_case_study_stub_body,
        )

        body = section.content or ""
        # Our Work designer-note cards are finished, not hollow — the approved
        # case study asset is the deliverable, so there is nothing to fill.
        if looks_like_case_study_stub_body(body):
            return True
        # Only treat as a gap if a bio stub wrongly landed on Our Work.
        return not looks_like_bio_stub_body(body)
    return False


def section_answers_missing(content: str) -> bool:
    """True when answers are absent — VERIFY tags not required."""
    body = (content or "").strip()
    if not body:
        return True
    if len(_EMPTY_LABELED_FIELD_RE.findall(body)) >= 1:
        return True
    if body.count("[MANUAL FILL") >= 1 and len(body.split()) < 120:
        return True
    if body.count("[MANUAL FILL") >= 3:
        return True
    if len(_PLACEHOLDER_VALUE_RE.findall(body)) >= 2:
        return True
    if len(_EMPTY_TABLE_CELL_RE.findall(body)) >= 2:
        return True
    if re.search(
        r"(?im)^#{2,4}\s+(?:Qualifications|Experience|References|Team)\s*$\n+"
        r"(?:#{2,4}\s|\Z)",
        body,
    ):
        return True
    return False


def _gap_reasons(content: str) -> list[str]:
    body = content or ""
    reasons: list[str] = []
    empty_fields = [m.group(1) for m in _EMPTY_LABELED_FIELD_RE.finditer(body)]
    if empty_fields:
        # Unique, preserve order
        seen: set[str] = set()
        labeled: list[str] = []
        for f in empty_fields:
            if f not in seen:
                seen.add(f)
                labeled.append(f)
        reasons.append("empty fields: " + ", ".join(labeled[:8]))
    n_manual = body.count("[MANUAL FILL")
    if n_manual:
        reasons.append(f"{n_manual} MANUAL FILL stub(s)")
    n_ph = len(_PLACEHOLDER_VALUE_RE.findall(body))
    if n_ph:
        reasons.append(f"{n_ph} TBD/placeholder line(s)")
    n_tbl = len(_EMPTY_TABLE_CELL_RE.findall(body))
    if n_tbl:
        reasons.append(f"{n_tbl} empty table row(s)")
    if not body.strip():
        reasons.append("section empty")
    return reasons or ["incomplete answers"]


def inventory_missing_answers(draft: ProposalDraft) -> list[MissingAnswerGap]:
    """Mechanical whole-proposal gap list — no LLM."""
    gaps: list[MissingAnswerGap] = []
    for section in draft.sections:
        if _skip_section(section):
            continue
        body = section.content or ""
        if not section_answers_missing(body):
            continue
        # Snippet: lines that look empty / stubby
        snip_lines: list[str] = []
        for line in body.splitlines():
            if (
                _EMPTY_LABELED_FIELD_RE.match(line)
                or "[MANUAL FILL" in line
                or _PLACEHOLDER_VALUE_RE.match(line)
            ):
                snip_lines.append(line.strip()[:120])
            if len(snip_lines) >= 6:
                break
        gaps.append(
            MissingAnswerGap(
                section_id=section.id or "",
                title=(section.title or section.id or "").strip(),
                reasons=_gap_reasons(body),
                snippet="\n".join(snip_lines)[:500],
            )
        )
    return gaps


def list_sections_needing_answer_fill(
    draft: ProposalDraft,
    *,
    limit: int = _MAX_PLAN_FILLS,
) -> list[ProposalSection]:
    """Compat helper — sections with missing answers (capped)."""
    want = {g.section_id for g in inventory_missing_answers(draft)[:limit]}
    return [s for s in draft.sections if (s.id or "") in want]


def _draft_context_block(draft: ProposalDraft, *, max_chars: int = 5_000) -> str:
    parts: list[str] = []
    for section in draft.sections:
        sid = section.id or ""
        title = (section.title or "").strip()
        if sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
            role = ""
            for line in (section.content or "").splitlines():
                if "Role on this engagement" in line:
                    role = line.strip()
                    break
            parts.append(f"{title}" + (f" | {role}" if role else ""))
        elif sid.startswith("section-3-"):
            parts.append(f"OUR WORK: {title}")
        elif sid.startswith("section-1-"):
            parts.append(f"COMPANY: {title}")
        if sum(len(p) for p in parts) > max_chars:
            break
    return "\n".join(parts)[:max_chars]


async def _plan_fills(
    gaps: list[MissingAnswerGap],
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
) -> list[dict[str, Any]]:
    """One planner call — which gaps to fill and which KB queries to run."""
    if not gaps:
        return []
    if not llm.is_configured():
        # No planner: take first N gaps with a default won-proposal query.
        out: list[dict[str, Any]] = []
        for gap in gaps[:_MAX_PLAN_FILLS]:
            out.append(
                {
                    "sectionId": gap.section_id,
                    "kbQuery": (
                        f"06_WON 07_FIN {rfp_sector} {gap.title} "
                        f"{' '.join(gap.reasons)[:80]}"
                    ).strip(),
                    "gaps": gap.reasons,
                }
            )
        return out

    inventory = "\n".join(
        f"- id={g.section_id} | {g.title} | {'; '.join(g.reasons)}\n"
        f"  snippet:\n  {g.snippet or '(empty)'}"
        for g in gaps[:20]
    )
    system = (
        "You are the missing-answers planner for a proposal manuscript.\n"
        "Given a mechanical inventory of gaps (empty fields, MANUAL FILL stubs, "
        "TBD lines, empty table cells — VERIFY tags are NOT required), pick up to "
        f"{_MAX_PLAN_FILLS} sections to fill now.\n"
        "For each, write ONE focused KB query preferring 06_WON / 07_FIN past won "
        "or finalist proposals, then 04_Bio / 03_CS.\n"
        "Skip gaps that are designer-only PDF handoffs or cannot be answered from KB.\n"
        "Do not invent facts. Return JSON only:\n"
        '{"fills":[{"sectionId":"...","kbQuery":"06_WON ...","gaps":["..."]}]}'
    )
    user = (
        f"RFP: {rfp_title}\nClient: {rfp_client}\nSector: {rfp_sector}\n\n"
        f"Gap inventory:\n{inventory}"
    )
    try:
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=2048,
            temperature=0.1,
            node_name="missing_answers_plan",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Missing-answers planner failed: %s", exc)
        return [
            {
                "sectionId": g.section_id,
                "kbQuery": f"06_WON 07_FIN {g.title}",
                "gaps": g.reasons,
            }
            for g in gaps[:_MAX_PLAN_FILLS]
        ]

    fills = raw.get("fills") if isinstance(raw, dict) else None
    if not isinstance(fills, list):
        return [
            {
                "sectionId": g.section_id,
                "kbQuery": f"06_WON 07_FIN {g.title}",
                "gaps": g.reasons,
            }
            for g in gaps[:_MAX_PLAN_FILLS]
        ]
    valid_ids = {g.section_id for g in gaps}
    planned: list[dict[str, Any]] = []
    for item in fills:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("sectionId") or item.get("section_id") or "").strip()
        if not sid or sid not in valid_ids:
            continue
        query = str(item.get("kbQuery") or item.get("kb_query") or "").strip()
        if not query:
            query = f"06_WON 07_FIN {sid}"
        planned.append(
            {
                "sectionId": sid,
                "kbQuery": query[:200],
                "gaps": item.get("gaps") or [],
            }
        )
        if len(planned) >= _MAX_PLAN_FILLS:
            break
    return planned


async def _retrieve_queries(queries: list[str]) -> tuple[str, list[str]]:
    """Run a small set of unique KB queries; merge into one evidence pack."""
    from app.services import supermemory
    from app.services.proposal_knowledge_base_tools import search_knowledge_base

    if not supermemory.is_configured() or not queries:
        return "", []

    seen_q: set[str] = set()
    blocks: list[str] = []
    sources: list[str] = []
    seen_src: set[str] = set()
    total = 0
    for raw_q in queries:
        q = " ".join((raw_q or "").split())
        key = q.casefold()
        if not q or key in seen_q:
            continue
        seen_q.add(key)
        if len(seen_q) > _MAX_KB_QUERIES:
            break
        try:
            text, srcs = await search_knowledge_base(
                q, limit=4, max_chars=_EVIDENCE_CHARS // _MAX_KB_QUERIES
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Missing-answers KB skip (%s): %s", q[:60], exc)
            continue
        if not (text or "").strip():
            continue
        for src in srcs or []:
            if src and src not in seen_src:
                seen_src.add(src)
                sources.append(src)
        remaining = _EVIDENCE_CHARS - total
        if remaining <= 0:
            break
        chunk = text[:remaining].strip()
        blocks.append(f"### KB query: {q[:90]}\n{chunk}")
        total += len(chunk)
    return "\n\n".join(blocks), sources


async def _llm_fill_section(
    *,
    section: ProposalSection,
    gaps: list[str],
    evidence: str,
    draft_context: str,
    rfp_title: str,
    rfp_client: str,
) -> str | None:
    if not llm.is_configured():
        return None
    body = (section.content or "").strip()
    system = (
        "Fill MISSING answers in ONE proposal section using ONLY the evidence pack.\n"
        "Rules:\n"
        "- Cover empty labeled fields, MANUAL FILL stubs, TBD lines, and empty table "
        "cells listed in the gap list — VERIFY tags are not required to act.\n"
        "- Keep substantive prose that is already good.\n"
        "- Prefer 06_WON / 07_FIN past won/finalist proposals, then 04_Bio / 03_CS / "
        "draft roster. Never invent clients, metrics, degrees, or contacts.\n"
        "- If evidence is thin for a field, use [VERIFY: …] — never fabricate.\n"
        "- No full resume dumps. Evaluator-ready markdown for THIS section only.\n"
        'Return JSON: {"content": "full markdown"}'
    )
    user = (
        f"RFP: {rfp_title}\nClient: {rfp_client}\n"
        f"Section: {section.title}\n"
        f"Gaps to fill: {gaps or ['missing answers']}\n\n"
        f"Current section:\n{body[:12_000]}\n\n"
        f"Draft roster / Our Work / company tabs:\n{draft_context[:5_000]}\n\n"
        f"KB evidence (won proposals + bios):\n{evidence[:_EVIDENCE_CHARS]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=_FILL_MAX_TOKENS,
            temperature=0.2,
            node_name="missing_answers_fill",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Missing-answers fill failed for %s: %s", section.id, exc)
        return None
    content = str((raw or {}).get("content") or "").strip()
    if not content or len(content) < 40:
        return None
    return content


async def fill_missing_answers_from_won_proposals(
    draft: ProposalDraft,
    *,
    rfp_title: str = "",
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_text: str = "",
    rfp_id: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Agentic pass: inventory → plan → retrieve → fill planned sections only."""
    del rfp_text
    gaps = inventory_missing_answers(draft)
    if not gaps:
        return draft, []

    logs: list[str] = [
        f"Missing-answers inventory: {len(gaps)} section(s) with gaps "
        "(empty fields / MANUAL FILL / TBD — VERIFY not required)"
    ]
    planned = await _plan_fills(
        gaps,
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
    )
    if not planned:
        logs.append("Missing-answers planner: nothing to fill this pass")
        return draft, logs

    queries = [str(p.get("kbQuery") or "") for p in planned]
    evidence, sources = await _retrieve_queries(queries)
    draft_ctx = _draft_context_block(draft)
    if not evidence.strip() and not draft_ctx.strip():
        logs.append("Missing-answers: no KB/draft evidence — gaps left open")
        return draft, logs

    by_id = {s.id: s for s in draft.sections}
    gap_by_id = {g.section_id: g for g in gaps}
    changed = False
    for item in planned:
        sid = str(item.get("sectionId") or "")
        section = by_id.get(sid)
        if section is None:
            continue
        gap = gap_by_id.get(sid)
        filled = await _llm_fill_section(
            section=section,
            gaps=list(item.get("gaps") or (gap.reasons if gap else [])),
            evidence=evidence or "(Use draft roster / Our Work only.)",
            draft_context=draft_ctx,
            rfp_title=rfp_title,
            rfp_client=rfp_client,
        )
        if not filled or filled.strip() == (section.content or "").strip():
            logs.append(f"«{section.title}»: fill skipped")
            continue
        by_id[sid] = section.model_copy(
            update={"content": filled, "status": "generated"}
        )
        logs.append(f"«{section.title}»: filled missing answers")
        changed = True
        logger.info(
            "missing_answers_fill rfp_id=%s section=%s sources=%s",
            rfp_id or "?",
            sid,
            sources[:4],
        )

    if sources:
        logs.append(f"Missing-answers KB sources used: {len(sources)}")
    if not changed:
        return draft, logs
    sections = [by_id.get(s.id, s) for s in draft.sections]
    return draft.model_copy(update={"sections": sections}), logs


async def fill_hollow_sections_for_pipeline(
    draft: ProposalDraft,
    *,
    rfp_title: str = "",
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_text: str = "",
    rfp_id: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Final-stage entry: free team skeleton heal, then agentic missing-answers."""
    from app.services.proposal_scan_fact_repairs import fill_hollow_project_team_from_bios

    draft, team_logs = fill_hollow_project_team_from_bios(draft)
    logs = list(team_logs)

    draft, agent_logs = await fill_missing_answers_from_won_proposals(
        draft,
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_text=rfp_text,
        rfp_id=rfp_id,
    )
    logs.extend(agent_logs)
    return draft, logs
