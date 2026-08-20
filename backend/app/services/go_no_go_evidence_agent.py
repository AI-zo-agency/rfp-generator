"""LLM evidence agent for Go/No-Go — plans Supermemory queries, then follows hits.

Cost model (intentionally lean):
  * ONE light query-plan call (what to search + why) per RFP.
  * ONE follow-up call ONLY when a core requirement still has zero hits.
  * No regex role lexicons, no duplicate seed search budgets.
  * Few sharp queries × 100 chunks each beats many vague searches.

Mechanical work only: run searches; downstream quote grounding blocks fabrication.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from app.services import llm
from app.services.go_no_go_requirements import RfpRequirement, all_queries

logger = logging.getLogger(__name__)

SearchFn = Callable[[str], Awaitable[list[dict[str, Any]]]]

QUERY_PLAN_PROMPT = """You plan Supermemory searches for zö agency Go/No-Go.

For EACH requirement: 1–2 sharp queries + a one-line why. Match query meaning
to the ask (press≠brand strategy; insurance≠audit; EEO≠WBENC badge).

KB tokens when useful: 01_companyfacts, 04_Bio, 03_CS, 06_WON.
Craft → bio AND case study. Compliance → companyfacts. Never search the buyer.

Return ONLY JSON:
{"plans":[{"requirement":"...","queries":["zö agency ..."],"why":"..."}]}"""

FOLLOW_UP_PROMPT = """Core requirements below have no KB hits. Propose 1–2 sharper
Supermemory queries each + why. Same KB rules: zö materials only, no buyer name.
Return ONLY JSON:
{"followUps":[{"requirement":"...","queries":["..."],"why":"..."}]}
Or {"followUps":[]} if nothing useful remains."""

# Tight budgets: accuracy from better queries + 100 chunks, not more LLM/API calls.
_MAX_INITIAL_QUERIES = 28
_MAX_FOLLOW_UP_QUERIES = 8
_MAX_QUERIES_PER_REQUIREMENT = 3
_DIGEST_CHARS_PER_HIT = 220
_DIGEST_HITS_PER_REQ = 2
_PLAN_RFP_CHARS = 4_000
_FOLLOW_RFP_CHARS = 2_000
_FOLLOW_DIGEST_CHARS = 12_000


def _clean_query(value: Any, *, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _hit_title(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("title")
        or metadata.get("title")
        or metadata.get("fileName")
        or hit.get("customId")
        or "untitled"
    )


def _hit_excerpt(hit: dict[str, Any], *, limit: int = _DIGEST_CHARS_PER_HIT) -> str:
    from app.services import supermemory

    body = supermemory.hit_text(hit) if hasattr(supermemory, "hit_text") else ""
    if not body:
        body = str(hit.get("content") or hit.get("text") or "")
    return re.sub(r"\s+", " ", body).strip()[:limit]


def _append_unique(bucket: list[str], candidates: list[str], *, limit: int) -> None:
    seen = {q.casefold() for q in bucket}
    for query in candidates:
        cleaned = _clean_query(query)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        bucket.append(cleaned)
        if len(bucket) >= limit:
            return


def select_initial_queries(
    requirements: list[RfpRequirement],
    *,
    max_queries: int = _MAX_INITIAL_QUERIES,
) -> list[str]:
    """Round-robin planned queries so every requirement gets a search."""
    selected: list[str] = []
    pointers = [0 for _ in requirements]
    while len(selected) < max_queries:
        progressed = False
        for idx, req in enumerate(requirements):
            if len(selected) >= max_queries:
                break
            qs = req.kb_queries or []
            while pointers[idx] < len(qs):
                candidate = qs[pointers[idx]]
                pointers[idx] += 1
                before = len(selected)
                _append_unique(selected, [candidate], limit=max_queries)
                if len(selected) > before:
                    progressed = True
                    break
        if not progressed:
            break
    return selected


def core_requirements_missing_hits(
    requirements: list[RfpRequirement],
    hits_by_requirement: dict[str, list[dict[str, Any]]],
) -> list[RfpRequirement]:
    """Only cores with zero hits justify a follow-up LLM call."""
    return [
        r
        for r in requirements
        if r.is_core and not (hits_by_requirement.get(r.requirement) or [])
    ]


def core_requirements_have_enough_hits(
    requirements: list[RfpRequirement],
    hits_by_requirement: dict[str, list[dict[str, Any]]],
    *,
    min_ratio: float = 0.72,
) -> bool:
    """Backward-compatible helper — prefer core_requirements_missing_hits."""
    core = [r for r in requirements if r.is_core]
    if not core:
        return True
    with_hits = sum(
        1 for r in core if (hits_by_requirement.get(r.requirement) or [])
    )
    return (with_hits / len(core)) >= min_ratio


def build_evidence_digest(
    requirements: list[RfpRequirement],
    hits_by_requirement: dict[str, list[dict[str, Any]]],
) -> str:
    """Compact view for the agent — enough to decide follow-ups, not a dump."""
    blocks: list[str] = []
    for req in requirements:
        name = req.requirement
        hits = hits_by_requirement.get(name) or []
        core = "core" if req.is_core else "optional"
        lines = [f"### {name} ({core})"]
        if not hits:
            lines.append("(no hits)")
        else:
            for hit in hits[:_DIGEST_HITS_PER_REQ]:
                lines.append(f"- {_hit_title(hit)}: {_hit_excerpt(hit)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _resolve_requirement_name(
    req_name: str,
    known_cf: dict[str, str],
) -> str | None:
    canonical = known_cf.get(req_name.casefold())
    if canonical:
        return canonical
    for key, name in known_cf.items():
        if req_name.casefold() in key or key in req_name.casefold():
            return name
    return None


def parse_query_plans(
    raw: dict[str, Any],
    *,
    known_requirements: set[str],
) -> list[tuple[str, list[str], str]]:
    """Return (requirement, queries, why) from the evidence agent's plan."""
    rows = raw.get("plans") or raw.get("queryPlans") or raw.get("query_plans") or []
    if not isinstance(rows, list):
        return []
    known_cf = {name.casefold(): name for name in known_requirements}
    out: list[tuple[str, list[str], str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        req_name = _clean_query(row.get("requirement"), limit=160)
        if not req_name:
            continue
        canonical = _resolve_requirement_name(req_name, known_cf)
        if not canonical:
            continue
        queries_raw = row.get("queries") or []
        queries: list[str] = []
        if isinstance(queries_raw, list):
            for item in queries_raw:
                cleaned = _clean_query(item)
                if cleaned:
                    queries.append(cleaned)
        queries = queries[:2]
        if not queries:
            continue
        why = _clean_query(row.get("why") or row.get("reason") or "", limit=160)
        out.append((canonical, queries, why))
    return out


def parse_follow_ups(
    raw: dict[str, Any],
    *,
    known_requirements: set[str],
) -> list[tuple[str, list[str]]]:
    """Return (requirement, queries) pairs the agent requested."""
    rows = raw.get("followUps") or raw.get("follow_ups") or []
    if not isinstance(rows, list):
        return []
    known_cf = {name.casefold(): name for name in known_requirements}
    out: list[tuple[str, list[str]]] = []
    total_q = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        req_name = _clean_query(row.get("requirement"), limit=160)
        if not req_name:
            continue
        canonical = _resolve_requirement_name(req_name, known_cf)
        if not canonical:
            continue
        queries_raw = row.get("queries") or []
        queries: list[str] = []
        if isinstance(queries_raw, list):
            for item in queries_raw:
                cleaned = _clean_query(item)
                if cleaned:
                    queries.append(cleaned)
        queries = queries[:2]
        if not queries:
            continue
        if total_q + len(queries) > _MAX_FOLLOW_UP_QUERIES:
            queries = queries[: max(0, _MAX_FOLLOW_UP_QUERIES - total_q)]
        if not queries:
            break
        why = _clean_query(row.get("why") or row.get("reason") or "", limit=120)
        if why:
            logger.info(
                "go_no_go evidence follow-up why [%s]: %s -> %s",
                canonical[:80],
                why[:120],
                "; ".join(queries)[:160],
            )
        out.append((canonical, queries))
        total_q += len(queries)
        if total_q >= _MAX_FOLLOW_UP_QUERIES:
            break
    return out


def apply_query_plans(
    requirements: list[RfpRequirement],
    plans: list[tuple[str, list[str], str]],
) -> list[RfpRequirement]:
    """Apply agent queries. Prefer planner output; keep one prior seed if uncovered."""
    by_name = {r.requirement: list(r.kb_queries) for r in requirements}
    for name, queries, why in plans:
        if name not in by_name:
            continue
        merged: list[str] = []
        _append_unique(merged, queries, limit=_MAX_QUERIES_PER_REQUIREMENT)
        by_name[name] = merged
        if why:
            logger.info(
                "go_no_go evidence plan why [%s]: %s -> %s",
                name[:80],
                why[:120],
                "; ".join(merged)[:160],
            )
    # Uncovered rows keep their requirement-planner seeds.
    return [
        req.model_copy(update={"kb_queries": by_name.get(req.requirement, req.kb_queries)})
        for req in requirements
    ]


def merge_queries_onto_requirements(
    requirements: list[RfpRequirement],
    follow_ups: list[tuple[str, list[str]]],
) -> list[RfpRequirement]:
    """Append agent follow-up queries onto the matching requirement rows."""
    by_name = {r.requirement: list(r.kb_queries) for r in requirements}
    for name, queries in follow_ups:
        existing = by_name.get(name)
        if existing is None:
            continue
        _append_unique(existing, queries, limit=_MAX_QUERIES_PER_REQUIREMENT)
        by_name[name] = existing
    return [
        req.model_copy(update={"kb_queries": by_name.get(req.requirement, req.kb_queries)})
        for req in requirements
    ]


def _requirements_digest_for_plan(requirements: list[RfpRequirement]) -> str:
    lines: list[str] = []
    for req in requirements:
        core = "core" if req.is_core else "optional"
        quote = (req.rfp_quote or "")[:100]
        lines.append(
            f"- {req.requirement} [{req.category}/{core}]"
            + (f" | quote: {quote}" if quote else "")
        )
    return "\n".join(lines)


async def plan_evidence_searches(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_excerpt: str,
    requirements: list[RfpRequirement],
) -> list[RfpRequirement]:
    """One light LLM call: what to query and why, per requirement."""
    if not requirements:
        return []
    messages = [
        {"role": "system", "content": QUERY_PLAN_PROMPT},
        {
            "role": "user",
            "content": (
                f"RFP: {rfp_title}\n"
                f"Excerpt:\n{(rfp_excerpt or '')[:_PLAN_RFP_CHARS]}\n\n"
                f"Requirements:\n{_requirements_digest_for_plan(requirements)}"
            ),
        },
    ]
    try:
        raw, provider = await llm.chat_json(
            messages,
            max_tokens=2048,
            temperature=0.1,
            tier="light",
            node_name="go_no_go_evidence_query_plan",
            rfp_id=rfp_id,
        )
        plans = parse_query_plans(
            raw if isinstance(raw, dict) else {},
            known_requirements={r.requirement for r in requirements},
        )
        logger.info(
            "go_no_go evidence query plan for %s via %s: %d/%d requirements",
            rfp_id,
            provider,
            len(plans),
            len(requirements),
        )
        if plans:
            return apply_query_plans(requirements, plans)
    except llm.LlmError as exc:
        logger.warning(
            "go_no_go evidence query plan failed for %s: %s — keeping prior queries",
            rfp_id,
            str(exc)[:160],
        )
    return list(requirements)


def attribute_hits(
    requirements: list[RfpRequirement],
    by_query: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Map each requirement to the hits its queries returned."""
    out: dict[str, list[dict[str, Any]]] = {}
    for req in requirements:
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for query in req.kb_queries:
            for hit in by_query.get(query.strip(), []):
                key = _hit_title(hit).casefold()
                if key in seen:
                    continue
                seen.add(key)
                collected.append(hit)
        out[req.requirement] = collected
    return out


async def run_evidence_agent(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_excerpt: str,
    requirements: list[RfpRequirement],
    search: SearchFn,
    max_initial: int = _MAX_INITIAL_QUERIES,
    max_follow_up: int = _MAX_FOLLOW_UP_QUERIES,
    allow_follow_up: bool = True,
    plan_queries: bool = True,
    initial_queries: list[str] | None = None,
) -> tuple[
    list[RfpRequirement],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[str],
]:
    """Plan searches (optional), execute, follow up only for empty cores.

    Returns (requirements_with_queries, hits_by_requirement, all_hits, queries_run).
    """
    if not requirements:
        return [], {}, [], []

    working = list(requirements)
    if plan_queries:
        working = await plan_evidence_searches(
            rfp_id=rfp_id,
            rfp_title=rfp_title,
            rfp_excerpt=rfp_excerpt,
            requirements=working,
        )

    if initial_queries:
        queries_run: list[str] = []
        _append_unique(queries_run, list(initial_queries), limit=max_initial)
        for q in select_initial_queries(working, max_queries=max_initial):
            _append_unique(queries_run, [q], limit=max_initial)
    else:
        queries_run = select_initial_queries(working, max_queries=max_initial)
    by_query: dict[str, list[dict[str, Any]]] = {}

    async def _run_batch(batch: list[str]) -> None:
        if not batch:
            return
        results = await asyncio.gather(*(search(q) for q in batch))
        for query, hits in zip(batch, results):
            by_query[query.strip()] = hits or []

    await _run_batch(queries_run)
    hits_by_req = attribute_hits(working, by_query)

    missing_cores = core_requirements_missing_hits(working, hits_by_req)
    if not missing_cores:
        logger.info(
            "go_no_go evidence agent skipping follow-up for %s — all cores have hits",
            rfp_id,
        )

    if allow_follow_up and max_follow_up > 0 and missing_cores:
        digest = build_evidence_digest(missing_cores, hits_by_req)
        messages = [
            {"role": "system", "content": FOLLOW_UP_PROMPT},
            {
                "role": "user",
                "content": (
                    f"RFP: {rfp_title}\n"
                    f"Excerpt:\n{(rfp_excerpt or '')[:_FOLLOW_RFP_CHARS]}\n\n"
                    f"Empty-core evidence:\n{digest[:_FOLLOW_DIGEST_CHARS]}"
                ),
            },
        ]
        try:
            raw, provider = await llm.chat_json(
                messages,
                max_tokens=1024,
                temperature=0.1,
                tier="light",
                node_name="go_no_go_evidence_agent",
                rfp_id=rfp_id,
            )
            follow_ups = parse_follow_ups(
                raw if isinstance(raw, dict) else {},
                known_requirements={r.requirement for r in missing_cores},
            )
            logger.info(
                "go_no_go evidence agent follow-ups for %s via %s: %d requirement(s)",
                rfp_id,
                provider,
                len(follow_ups),
            )
        except llm.LlmError as exc:
            logger.warning(
                "go_no_go evidence agent follow-up failed for %s: %s",
                rfp_id,
                str(exc)[:160],
            )
            follow_ups = []

        if follow_ups:
            working = merge_queries_onto_requirements(working, follow_ups)
            new_queries: list[str] = []
            _append_unique(
                new_queries,
                [q for _name, qs in follow_ups for q in qs],
                limit=max_follow_up,
            )
            new_queries = [
                q for q in new_queries if q.strip() not in by_query
            ][:max_follow_up]
            await _run_batch(new_queries)
            queries_run.extend(new_queries)
            hits_by_req = attribute_hits(working, by_query)

    all_hits: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    hit_groups = list(hits_by_req.values()) + list(by_query.values())
    for hits in hit_groups:
        for hit in hits:
            key = _hit_title(hit).casefold()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            all_hits.append(hit)

    logger.info(
        "go_no_go evidence agent for %s: %d queries, %d requirements, %d hits "
        "(planned queries available=%d)",
        rfp_id,
        len(queries_run),
        len(working),
        len(all_hits),
        len(all_queries(working)),
    )
    return working, hits_by_req, all_hits, queries_run
