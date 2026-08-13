"""LLM evidence agent for Go/No-Go — decides Supermemory queries, then follows the hits.

The old path mixed planner queries with regex role lexicons and deterministic
extras. That starved some requirements and still missed platform bios when the
wrong strings ran first. Here the model owns the search plan:

  1. Plan requirements + initial queries (upstream).
  2. Execute those searches against Supermemory.
  3. Show the agent a compact evidence digest.
  4. Agent may request follow-up queries for thin/missing rows.
  5. Downstream adjudicator judges ONLY from retrieved text + grounded quotes.

No named-person anchors, no client-specific regex, no hard-coded case studies.
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

FOLLOW_UP_PROMPT = """You are the Go/No-Go evidence agent for zö agency.

You already planned RFP requirements and ran knowledge-base searches. Below is
what came back (document titles + short excerpts), grouped by requirement.

Decide whether ANY requirement still needs better evidence. If yes, propose
follow-up Supermemory search queries. If evidence is already enough to judge
(verified / partial / gap), request nothing for that row.

Rules:
- Query the knowledge base the way zö materials are written: job titles, tools,
  platforms, deliverables, bio/case-study tokens (04_Bio, 03_CS, 06_WON,
  01_companyfacts). Never use the RFP buyer's name as the search subject.
- Prefer people (bios) AND projects (case studies / won proposals) for technical
  platform skills. A specialist bio stating a tool IS valid evidence for that tool.
- Do not invent documents. You are only choosing search strings.
- Max 2 follow-up queries per thin requirement. Max 12 follow-ups total.
- Skip logistics you already have enough on; focus on core technical/role rows
  with empty or weak hits.

Return ONLY JSON:
{"followUps":[{"requirement":"...","queries":["zö agency ...","..."],
  "why":"one short reason"}]}
If nothing more is needed: {"followUps":[]}"""

_MAX_INITIAL_QUERIES = 20
_MAX_FOLLOW_UP_QUERIES = 12
_MAX_QUERIES_PER_REQUIREMENT = 4
_DIGEST_CHARS_PER_HIT = 280
_DIGEST_HITS_PER_REQ = 3


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
    """Round-robin LLM-planned queries so every requirement gets a search."""
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
        canonical = known_cf.get(req_name.casefold())
        if not canonical:
            for key, name in known_cf.items():
                if req_name.casefold() in key or key in req_name.casefold():
                    canonical = name
                    break
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
        out.append((canonical, queries))
        total_q += len(queries)
        if total_q >= _MAX_FOLLOW_UP_QUERIES:
            break
    return out


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
) -> tuple[
    list[RfpRequirement],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[str],
]:
    """Execute LLM-planned searches, optionally follow up, return evidence maps.

    Returns (requirements_with_queries, hits_by_requirement, all_hits, queries_run).
    """
    if not requirements:
        return [], {}, [], []

    queries_run: list[str] = select_initial_queries(requirements, max_queries=max_initial)
    by_query: dict[str, list[dict[str, Any]]] = {}

    async def _run_batch(batch: list[str]) -> None:
        if not batch:
            return
        results = await asyncio.gather(*(search(q) for q in batch))
        for query, hits in zip(batch, results):
            by_query[query.strip()] = hits or []

    await _run_batch(queries_run)
    working = list(requirements)
    hits_by_req = attribute_hits(working, by_query)

    if allow_follow_up and max_follow_up > 0:
        digest = build_evidence_digest(working, hits_by_req)
        messages = [
            {"role": "system", "content": FOLLOW_UP_PROMPT},
            {
                "role": "user",
                "content": (
                    f"RFP: {rfp_title}\n"
                    f"Excerpt (context only):\n{(rfp_excerpt or '')[:6_000]}\n\n"
                    f"Evidence so far:\n{digest[:40_000]}"
                ),
            },
        ]
        try:
            raw, provider = await llm.chat_json(
                messages,
                max_tokens=2048,
                temperature=0.1,
                node_name="go_no_go_evidence_agent",
                rfp_id=rfp_id,
            )
            follow_ups = parse_follow_ups(
                raw if isinstance(raw, dict) else {},
                known_requirements={r.requirement for r in working},
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
    for hits in hits_by_req.values():
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
