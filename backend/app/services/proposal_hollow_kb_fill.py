"""Agentic missing-answer fill for Generate + Complete Scan final stage.

Whole-proposal mechanical inventory → one planner LLM → few KB queries →
fill only the planned sections. Not a bulk rewrite of every tab.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import llm
from app.services.proposal_draft_structure_stubs import (
    content_looks_like_instructional_checklist as _looks_like_instructional_checklist,
)
from app.services.proposal_manual_flags import sanitize_bare_bracket_tag_words

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
# Each gap can now plan up to 3 queries instead of 1 (see _plan_fills) — raise
# the search cap to match, so a 6-section batch isn't still bottlenecked down
# to 3 total searches. Supermemory lookups are cheap, non-LLM calls; raising
# this does not add LLM cost. _EVIDENCE_CHARS is unchanged, so the fill LLM
# call's input size does not grow either — more, smaller, better-targeted
# slices fill the exact same total budget instead of a few generic ones.
_MAX_KB_QUERIES = 8
_EVIDENCE_CHARS = 14_000
_FILL_MAX_TOKENS = 16000

# Hard ceilings so this stage can never hang indefinitely — an item that blows
# its budget is skipped (left as a gap for the next pass) rather than stalling
# the whole "Complete & clean draft" pipeline. See proposal_fulfill_rfp_gaps.py
# step 17 ("Pre-submit refresh"), the step this used to freeze on.
_PLAN_CALL_TIMEOUT_SEC = 150.0
_FILL_CALL_TIMEOUT_SEC = 150.0
_RETRIEVAL_QUERY_TIMEOUT_SEC = 60.0
_STEP_TIME_BUDGET_SEC = 480.0

# Section fills are independent of each other (shared evidence pack fixed
# before the loop starts) — run a few at a time instead of one-at-a-time.
# Kept modest to stay well inside provider rate limits.
_MAX_CONCURRENT_FILLS = 3


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
    known_case_study_clients: list[str] | None = None,
) -> list[dict[str, Any]]:
    """One planner call — which gaps to fill and which KB queries to run each.

    Each gap gets up to 3 queries, not 1 — the automated pass was giving each
    section one shot at a vague query while the chat "Improve section" flow
    runs several targeted ones per ask; this is that same idea applied here,
    still one planner call and the evidence budget (_EVIDENCE_CHARS) is
    unchanged, so this does not add an LLM call or grow the fill prompt.
    """
    if not gaps:
        return []
    if not llm.is_configured():
        # No planner: take first N gaps with a default won-proposal query.
        out: list[dict[str, Any]] = []
        for gap in gaps[:_MAX_PLAN_FILLS]:
            out.append(
                {
                    "sectionId": gap.section_id,
                    "kbQueries": [
                        (
                            f"06_WON 07_FIN {rfp_sector} {gap.title} "
                            f"{' '.join(gap.reasons)[:80]}"
                        ).strip()
                    ],
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
        "For each, write 1-3 focused KB queries — not a single generic one. "
        "Prefer 06_WON / 07_FIN past won or finalist proposals, then 04_Bio / "
        "03_CS. When the client/case-study names already in this draft (listed "
        "below, if any) are relevant to the gap, name them directly in at "
        "least one query instead of only a generic gap-title search — a query "
        "naming the real client finds their reference/contact details far "
        "better than a query built from the RFP's own generic field label.\n"
        "Skip gaps that are designer-only PDF handoffs or cannot be answered from KB.\n"
        "Do not invent facts. Return JSON only:\n"
        '{"fills":[{"sectionId":"...","kbQueries":["06_WON ...","..."],'
        '"gaps":["..."]}]}'
    )
    case_studies_block = (
        "\n".join(f"- {c}" for c in (known_case_study_clients or [])[:20])
        or "(none)"
    )
    user = (
        f"RFP: {rfp_title}\nClient: {rfp_client}\nSector: {rfp_sector}\n\n"
        f"Client/case-study names already in this draft:\n{case_studies_block}\n\n"
        f"Gap inventory:\n{inventory}"
    )
    try:
        raw, _ = await asyncio.wait_for(
            llm.chat_json(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=16000,
                temperature=0.1,
                node_name="missing_answers_plan",
            ),
            timeout=_PLAN_CALL_TIMEOUT_SEC,
        )
    except Exception as exc:  # noqa: BLE001 — includes asyncio.TimeoutError
        logger.warning("Missing-answers planner failed: %s", exc)
        return [
            {
                "sectionId": g.section_id,
                "kbQueries": [f"06_WON 07_FIN {g.title}"],
                "gaps": g.reasons,
            }
            for g in gaps[:_MAX_PLAN_FILLS]
        ]

    fills = raw.get("fills") if isinstance(raw, dict) else None
    if not isinstance(fills, list):
        return [
            {
                "sectionId": g.section_id,
                "kbQueries": [f"06_WON 07_FIN {g.title}"],
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
        raw_queries = item.get("kbQueries") or item.get("kb_queries")
        queries: list[str] = []
        if isinstance(raw_queries, list):
            queries = [str(q).strip()[:200] for q in raw_queries if str(q).strip()][:3]
        if not queries:
            # Back-compat with a model that still returns the old single-query
            # shape, and the ultimate fallback if it returns neither.
            single = str(item.get("kbQuery") or item.get("kb_query") or "").strip()
            queries = [single[:200]] if single else [f"06_WON 07_FIN {sid}"]
        planned.append(
            {
                "sectionId": sid,
                "kbQueries": queries,
                "gaps": item.get("gaps") or [],
            }
        )
        if len(planned) >= _MAX_PLAN_FILLS:
            break
    return planned


async def _retrieve_queries(queries: list[str]) -> tuple[str, list[str]]:
    """Run a small set of unique KB queries concurrently; merge into one evidence pack."""
    from app.services import supermemory
    from app.services.proposal_knowledge_base_tools import search_knowledge_base

    if not supermemory.is_configured() or not queries:
        return "", []

    seen_q: set[str] = set()
    unique_queries: list[str] = []
    for raw_q in queries:
        q = " ".join((raw_q or "").split())
        key = q.casefold()
        if not q or key in seen_q:
            continue
        seen_q.add(key)
        unique_queries.append(q)
        if len(unique_queries) >= _MAX_KB_QUERIES:
            break

    async def _fetch(q: str) -> tuple[str, str, list[str]]:
        try:
            text, srcs = await asyncio.wait_for(
                search_knowledge_base(
                    q, limit=4, max_chars=_EVIDENCE_CHARS // _MAX_KB_QUERIES
                ),
                timeout=_RETRIEVAL_QUERY_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001 — includes asyncio.TimeoutError
            logger.debug("Missing-answers KB skip (%s): %s", q[:60], exc)
            return q, "", []
        return q, text, srcs

    # Independent lookups — fire them together instead of one-at-a-time, then
    # merge in the original order so truncation/dedup stays deterministic.
    results = await asyncio.gather(*(_fetch(q) for q in unique_queries))

    blocks: list[str] = []
    sources: list[str] = []
    seen_src: set[str] = set()
    total = 0
    for q, text, srcs in results:
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
        "- Never write a checklist, numbered to-do list, or description of what "
        "someone else should do instead (\"confirm whether...\", \"select three "
        "references...\", \"obtain contact info...\") — that is process narration, "
        "not content, and this section ships to the client as-is. Commit to real "
        "structure using the named facts you DO have (e.g. specific client / "
        "case-study names already in the draft context) and mark only the "
        "individual missing facts inline as [VERIFY: field] on that same entry — "
        "a reference entry naming the real client with [VERIFY: contact name], "
        "[VERIFY: phone], [VERIFY: email] beats a generic instructions list "
        "every time.\n"
        "- No full resume dumps. Evaluator-ready markdown for THIS section only.\n"
        "- These rules govern how you write; they are never content. Never write "
        "sentences about verification requirements or your own constraints — apply "
        "the rule silently. The tag is the only trace of a gap; never explain or "
        "preface it.\n"
        'Return JSON: {"content": "full markdown"}'
    )
    user = (
        f"RFP: {rfp_title}\nClient: {rfp_client}\n"
        f"Section: {section.title}\n"
        f"Gaps to fill: {gaps or ['missing answers']}\n\n"
        f"Current section:\n{body[:12_000]}"
    )
    # draft_context/evidence are the same across every section this batch fills
    # (built once in fill_missing_answers_from_won_proposals before the fan-out) —
    # cache them instead of re-sending them fresh on every concurrent call.
    # cache_prefix segments are separate content blocks with no separator
    # auto-inserted between them (or before the user tail) — each must carry its
    # own trailing blank line to keep the same spacing the inlined version had.
    cache_prefix = [
        f"Draft roster / Our Work / company tabs:\n{draft_context[:5_000]}\n\n",
        f"KB evidence (won proposals + bios):\n{evidence[:_EVIDENCE_CHARS]}\n\n",
    ]
    try:
        raw, _ = await asyncio.wait_for(
            llm.chat_json(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=_FILL_MAX_TOKENS,
                temperature=0.2,
                node_name="missing_answers_fill",
                cache_prefix=cache_prefix,
            ),
            timeout=_FILL_CALL_TIMEOUT_SEC,
        )
    except Exception as exc:  # noqa: BLE001 — includes asyncio.TimeoutError
        logger.warning("Missing-answers fill failed for %s: %s", section.id, exc)
        return None
    content = str((raw or {}).get("content") or "").strip()
    if not content or len(content) < 40:
        return None
    content = sanitize_bare_bracket_tag_words(content)
    if _looks_like_instructional_checklist(content):
        logger.warning(
            "Missing-answers fill for %s wrote a to-do checklist instead of "
            "content — rejected, section left as-is for a later pass",
            section.id,
        )
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
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    on_cancel_check: Callable[[], Awaitable[None]] | None = None,
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
    draft_ctx = _draft_context_block(draft)
    known_case_study_clients = [
        line[len("OUR WORK: ") :].strip()
        for line in draft_ctx.splitlines()
        if line.startswith("OUR WORK: ")
    ]
    planned = await _plan_fills(
        gaps,
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        known_case_study_clients=known_case_study_clients,
    )
    if not planned:
        logs.append("Missing-answers planner: nothing to fill this pass")
        return draft, logs

    queries = [q for p in planned for q in (p.get("kbQueries") or [])]
    evidence, sources = await _retrieve_queries(queries)
    if not evidence.strip() and not draft_ctx.strip():
        logs.append("Missing-answers: no KB/draft evidence — gaps left open")
        return draft, logs

    by_id = {s.id: s for s in draft.sections}
    gap_by_id = {g.section_id: g for g in gaps}
    changed = False

    # Each item fills a different section from a shared, already-fixed evidence
    # pack — they don't depend on one another's output, so run them concurrently
    # (capped) instead of one-at-a-time. Same accuracy, a fraction of the wall
    # clock time.
    sem = asyncio.Semaphore(_MAX_CONCURRENT_FILLS)

    async def _fill_one(item: dict[str, Any]) -> tuple[str, str | None]:
        sid = str(item.get("sectionId") or "")
        section = by_id.get(sid)
        if section is None:
            return sid, None
        async with sem:
            if on_cancel_check:
                await on_cancel_check()
            gap = gap_by_id.get(sid)
            filled = await _llm_fill_section(
                section=section,
                gaps=list(item.get("gaps") or (gap.reasons if gap else [])),
                evidence=evidence or "(Use draft roster / Our Work only.)",
                draft_context=draft_ctx,
                rfp_title=rfp_title,
                rfp_client=rfp_client,
            )
            return sid, filled

    tasks = {asyncio.ensure_future(_fill_one(item)): item for item in planned}
    done, pending = await asyncio.wait(tasks.keys(), timeout=_STEP_TIME_BUDGET_SEC)

    if pending:
        for task in pending:
            task.cancel()
        logs.append(
            f"Missing-answers: time budget reached — filled {len(done)}/{len(planned)}, "
            f"{len(pending)} section(s) left for the next pass"
        )
        logger.warning(
            "missing_answers_fill rfp_id=%s time budget (%ss) reached — %d/%d done",
            rfp_id or "?",
            _STEP_TIME_BUDGET_SEC,
            len(done),
            len(planned),
        )

    completed = 0
    first_exc: BaseException | None = None
    for task in done:
        exc = task.exception()
        if exc is not None:
            # A cooperative Stop request (or unexpected error) surfaced inside
            # a concurrent fill. Remember it and keep draining the other
            # already-finished tasks below so their fills still land on
            # `by_id`; raised at the end, same as the old sequential loop
            # (which also discarded this function's own in-progress work on
            # cancellation — steps before this one are already persisted).
            first_exc = first_exc or exc
            continue
        sid, filled = task.result()
        completed += 1
        section = by_id.get(sid)
        if on_progress:
            await on_progress(completed, len(planned), (section.title if section else sid) or sid)
        if section is None:
            continue
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

    if first_exc is not None:
        raise first_exc

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
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    on_cancel_check: Callable[[], Awaitable[None]] | None = None,
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
        on_progress=on_progress,
        on_cancel_check=on_cancel_check,
    )
    logs.extend(agent_logs)
    return draft, logs
