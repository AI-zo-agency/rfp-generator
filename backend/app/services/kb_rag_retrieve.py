"""KB RAG helpers: thorough Supermemory fetch + context packing.

Searches v4 documents (raw chunks) first, then hybrid memories as gap-fill.
Chunk text carries KPIs, tables, and section detail that memory summaries omit.
Full documents are packed when they fit.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Memories carry one-line facts (a rate, a correction) that chunk search never
# surfaces on its own. Left to compete with chunks on raw rank or per-doc budget,
# a handful of long proposal chunks fill the rank cut and the context budget
# before a short memory fact is ever reached — so memories get a reserved floor
# in the rank cut (MEMORY_FLOOR) and a small dedicated budget in the packing
# loop (MEMORY_BUDGET_CHARS) that chunk hits may not spend.
MEMORY_FLOOR = 3
MEMORY_BUDGET_CHARS = 1_500

# Minimal function words for overlap scoring only — not used to invent queries.
_STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "in",
    "on",
    "of",
    "for",
    "to",
    "is",
    "are",
    "any",
    "with",
    "from",
    "this",
    "that",
}


def _question_terms(question: str) -> list[str]:
    terms = [
        t
        for t in re.findall(r"[a-z0-9+]{3,}", (question or "").casefold())
        if t not in _STOP
    ]
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


_BOILERPLATE_PREFIX = (
    "Find zö agency knowledge-base facts, case studies, and KPIs for a proposal section."
)

# Identical Supermemory searches during Complete & Clean (same truncated prefix,
# same pricing-guide fallback) — cache for the process lifetime of a scan.
_SEARCH_CACHE_MAX = 80
_search_hit_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
_search_inflight: dict[tuple[Any, ...], asyncio.Future[list[dict[str, Any]]]] = {}


def search_head_for_supermemory(question: str, *, max_len: int = 220) -> str:
    """Distinctive head sent to Supermemory (title/ask first).

    A long boilerplate prefix used to lead every question. v4 truncates around
    ~80 characters, so every section searched the same string and missed.
    """
    q = (question or "").strip()
    if not q:
        return ""
    idx = q.find(_BOILERPLATE_PREFIX)
    if idx == 0:
        q = q[len(_BOILERPLATE_PREFIX) :].strip() or q
    elif idx > 0:
        q = q[:idx].strip(" .")
    return q[:max_len].strip(" .")


def expand_kb_queries(question: str, *, max_queries: int = 4) -> list[str]:
    """User question plus targeted supplemental queries (budget guide, Oregon clients)."""
    q = (question or "").strip()
    if not q:
        return []
    search = search_head_for_supermemory(q)
    intent = search.split("Why needed:", 1)[0].casefold()
    queries = [search]

    budget_kw = {"budget", "pricing", "price", "rate", "fee", "cost", "hourly"}
    if any(kw in intent for kw in budget_kw):
        queries.append("zö agency pricing guide rates fees hourly")

    if "oregon" in intent:
        queries.append(
            "Oregon Employment Umatilla Lake Oswego Bend Deschutes proposal budget"
        )

    seen: set[str] = set()
    out: list[str] = []
    for item in queries:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:max_queries]


def build_retrieval_question_from_entry(
    *,
    section_id: str = "",
    section_title: str = "",
    required_assets: list[str] | None = None,
    planner_queries: list[str] | None = None,
    why_needed: str = "",
    rfp_client: str = "",
) -> str:
    """Natural-language KB question (same shape as kb_qa_loop / section chat pack).

    Phase 2 retrieval plans often emit keyword fragments; Supermemory works best
    with one clear question like the manual KB QA loop uses.
    """
    # Distinctive text MUST lead. Supermemory truncates the query; a boilerplate
    # prefix made every section search identical and return 0 hits.
    parts: list[str] = []
    title = (section_title or "").strip()
    if title:
        parts.append(f'Section: "{title}".')
    client = (rfp_client or "").strip()
    if client:
        parts.append(f"RFP client context: {client}.")
    queries = [str(q).strip() for q in (planner_queries or []) if str(q).strip()]
    if queries:
        focus = queries[0]
        if len(focus) >= 24 and " " in focus:
            parts.append(f"Search focus: {focus[:400]}.")
        else:
            parts.append(f"Topics: {', '.join(queries[:3])}.")
    assets = [str(a).strip() for a in (required_assets or []) if str(a).strip()][:8]
    if assets:
        parts.append("Required proof/assets: " + "; ".join(assets) + ".")
    why = (why_needed or "").strip()
    if why:
        parts.append(f"Why needed: {why[:400]}.")
    if section_id:
        parts.append(f"(section id {section_id})")
    parts.append(_BOILERPLATE_PREFIX)
    parts.append(
        "Prefer 03_CS case studies and won proposal excerpts (06_WON/07_FIN Proposal). "
        "Include strategy, deliverables, and measurable results/KPIs when present. "
        "Do not invent clients, numbers, or certifications."
    )
    return " ".join(parts)[:1200]


def context_blocks_to_hits(context: str, sources: list[str] | None = None) -> list[dict[str, Any]]:
    """Split packed RAG context (### filename blocks) into pseudo-hits for JIT corpus."""
    text = (context or "").strip()
    if not text or text.startswith("(No matching"):
        return []
    hits: list[dict[str, Any]] = []
    chunks = re.split(r"(?m)^### ", text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split("\n", 1)
        label = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if not label or not body:
            continue
        hits.append(
            {
                "title": label,
                "metadata": {"fileName": label},
                "customId": label,
                "content": body,
                "excerpt": body,
                "source": label,
            }
        )
    if hits:
        return hits
    # Single blob without headers — attach first source label if we have one.
    label = (sources or ["knowledge_base"])[0]
    return [
        {
            "title": label,
            "metadata": {"fileName": label},
            "customId": label,
            "content": text,
            "excerpt": text,
            "source": label,
        }
    ]


def is_source_rfp_filename(name: str) -> bool:
    """True for client solicitation PDFs stored alongside won proposals."""
    n = (name or "").casefold()
    if "_rfp_" in n or n.endswith("_rfp.pdf"):
        return True
    if re.search(r"(?:^|[_-])rfp(?:[_-]|\.pdf$)", n) and "proposal" not in n:
        return True
    if re.search(r"\brfp\b", n) and "proposal" not in n:
        return True
    return False


def prefer_agency_evidence_filename(name: str) -> float:
    """Higher = prefer agency Proposal / case-study files over source RFPs."""
    n = (name or "").casefold()
    score = 0.0
    if n.startswith("03_cs") or "/03_cs" in n:
        score += 4.0
    if "proposal" in n and "rfp" not in n:
        score += 3.5
    if n.startswith("07_fin") or n.startswith("06_won"):
        score += 2.0
    if n.startswith("01_") or "companyfacts" in n or "mastertemplate" in n:
        score += 2.5
    if n.startswith("04_bio"):
        score += 2.0
    if is_source_rfp_filename(name):
        score -= 4.0
    if "filingguide" in n or "claude_knowledge" in n:
        score -= 1.5
    if "00_guide" in n and "pricing" not in n and "writing" not in n:
        score -= 1.5
    if "00_guide_pricing" in n or "guide_pricing" in n:
        score += 2.0
    return score


def _hit_label(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    file_name = str(meta.get("fileName") or "").strip()
    if file_name:
        return file_name
    custom = str(hit.get("customId") or meta.get("customId") or "").strip()
    if custom:
        tail = custom.rsplit("/", 1)[-1]
        if "." in tail or tail.startswith(("01_", "02_", "03_", "04_", "06_", "07_")):
            return tail
    title = str(hit.get("title") or meta.get("title") or "").strip()
    if title and title.casefold() != "untitled document":
        return title
    return custom or title or ""


def _looks_like_kb_filename(name: str) -> bool:
    n = (name or "").casefold()
    if not n or n == "untitled document":
        return False
    if n.startswith(("01_", "02_", "03_", "04_", "06_", "07_", "00_")):
        return True
    return bool(re.search(r"\.(pdf|docx?|md|txt)$", n))


def _hit_snippet(hit: dict[str, Any]) -> str:
    for key in ("chunk", "chunks", "content", "text", "memory", "summary"):
        value = hit.get(key)
        if value:
            if isinstance(value, list):
                return "\n".join(str(v) for v in value).strip()
            return str(value).strip()
    documents = hit.get("documents")
    if isinstance(documents, list):
        for document in documents:
            if not isinstance(document, dict):
                continue
            for key in ("chunk", "content", "text"):
                value = document.get(key)
                if value:
                    return str(value).strip()
    return ""


def _hit_score(hit: dict[str, Any]) -> float:
    for key in ("similarity", "score", "rerankScore"):
        value = hit.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _term_overlap(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    cf = (text or "").casefold()
    hits = sum(1 for t in terms if t in cf)
    return hits / max(len(terms), 1)


def rank_hits_for_question(
    hits: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    """Re-rank by filename preference + term overlap (no topic hardcoding)."""
    from app.services import supermemory

    terms = _question_terms(question)
    q_cf = (question or "").casefold()
    ask_about_rfp = bool(re.search(r"\brfp\b|solicitation", q_cf))
    _BUDGET_KW = {"budget", "pricing", "price", "rate", "fee", "cost", "hourly"}
    ask_about_budget = any(kw in q_cf for kw in _BUDGET_KW)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, hit in enumerate(hits):
        label = _hit_label(hit)
        snippet = _hit_snippet(hit)
        overlap = _term_overlap(f"{label} {snippet}", terms)
        if is_source_rfp_filename(label) and not ask_about_rfp:
            continue
        if not _looks_like_kb_filename(label) and overlap < 0.2:
            continue
        rank = (
            prefer_agency_evidence_filename(label) * 1.2
            + overlap * 8.0
            + _hit_score(hit)
            + (1.5 if _looks_like_kb_filename(label) else -2.0)
        )
        # Pricing guide is THE authoritative source for any budget/pricing question
        if ask_about_budget and "guide_pricing" in label.casefold():
            rank += 20.0
        if supermemory.is_chunk_hit(hit):
            rank += 3.0
        elif supermemory.is_memory_hit(hit):
            rank -= 1.5
        # Oregon client work lives under client-specific proposal/case-study filenames
        if "oregon" in q_cf:
            label_cf = label.casefold()
            if any(
                tok in label_cf
                for tok in (
                    "oregon",
                    "umatilla",
                    "lakeoswego",
                    "bend",
                    "deschutes",
                    "mcminnville",
                )
            ):
                rank += 4.0
        # Boost when the filename itself matches question tokens (e.g. TorrentLaboratories)
        if label and _term_overlap(label, terms) > 0:
            rank += 3.0
        scored.append((rank, index, hit))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [hit for _, _, hit in scored]


def extract_relevant_windows(
    document: str,
    question: str,
    *,
    max_chars: int = 12_000,
    window: int = 1200,
) -> str:
    """Pull text windows around query-term matches."""
    text = document or ""
    if not text.strip():
        return ""
    terms = _question_terms(question)
    if not terms:
        return text[:max_chars]

    cf = text.casefold()
    ranked_terms = sorted(terms, key=lambda t: (-len(t), t))
    centers: list[int] = []
    for term in ranked_terms:
        start = 0
        found_for_term = 0
        while found_for_term < 4:
            idx = cf.find(term, start)
            if idx < 0:
                break
            centers.append(idx)
            start = idx + len(term)
            found_for_term += 1
            if len(centers) >= 16:
                break
        if len(centers) >= 16:
            break

    if not centers:
        return text[:max_chars]

    spans: list[tuple[int, int]] = []
    for center in centers:
        lo = max(0, center - window // 3)
        hi = min(len(text), center + window)
        spans.append((lo, hi))
    spans.sort()

    merged: list[tuple[int, int]] = []
    for lo, hi in spans:
        if not merged or lo > merged[-1][1] + 80:
            merged.append((lo, hi))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))

    parts: list[str] = []
    total = 0
    for lo, hi in merged:
        chunk = text[lo:hi].strip()
        if not chunk:
            continue
        piece = chunk if lo == 0 else f"…\n{chunk}"
        if total + len(piece) > max_chars:
            remain = max_chars - total
            if remain < 200:
                break
            parts.append(piece[:remain])
            break
        parts.append(piece)
        total += len(piece)
    return "\n\n".join(parts).strip()


def pack_hit_context(
    hit: dict[str, Any],
    *,
    full_document: str,
    question: str,
    max_chars: int,
) -> str:
    """Prefer the full indexed document when it fits; otherwise snippet + windows.

    Critical: never discard later sections just because the search snippet was
    only the document intro.
    """
    from app.services import supermemory as _sm

    label = _hit_label(hit) or "document"
    source_tag = ""
    if _sm.is_memory_hit(hit):
        source_tag = " ⚠️ MEMORY SUMMARY — do NOT cite exact numbers from this; use [VERIFY] instead"
    header = f"### {label}{source_tag}"
    full = (full_document or "").strip()
    snippet = _hit_snippet(hit).strip()

    # Thorough path: whole document fits → send all of it
    if full and len(header) + 1 + len(full) <= max_chars:
        return f"{header}\n{full}"

    parts: list[str] = [header]
    used = len(header) + 1
    snippet_cf = snippet.casefold()

    if snippet:
        block = snippet[: min(700, max(350, max_chars // 5))]
        parts.append(block)
        used += len(block) + 1

    remaining = max_chars - used
    if remaining > 300 and full:
        # Truncate from start only as last resort — prefer term windows
        windows = extract_relevant_windows(full, question, max_chars=remaining)
        if not windows.strip():
            windows = full[:remaining]
        extra = windows
        win_cf = windows.casefold()
        snip_prefix = snippet_cf[:180].strip()
        if snip_prefix and win_cf.startswith(snip_prefix):
            extra = windows[len(snip_prefix) :].lstrip(" .\n…")
        elif snippet_cf and win_cf in snippet_cf:
            extra = ""
        if extra.strip():
            parts.append(extra if extra.startswith("…") else f"…\n{extra}")

    return "\n".join(parts).strip()[:max_chars]


def _search_cache_key(
    query: str,
    *,
    limit: int,
    filters: dict[str, Any] | None,
    threshold: float,
) -> tuple[Any, ...]:
    filt = ""
    if isinstance(filters, dict):
        filt = str(sorted(filters.items()))
    return (query.strip(), int(limit), round(float(threshold), 3), filt)


async def _search_hits_chunk_first(
    query: str,
    *,
    limit: int,
    filters: dict[str, Any] | None,
    threshold: float,
) -> list[dict[str, Any]]:
    """Fetch more raw chunks than memory summaries; chunks lead the merged list."""
    from app.services import supermemory

    key = _search_cache_key(query, limit=limit, filters=filters, threshold=threshold)
    cached = _search_hit_cache.get(key)
    if cached is not None:
        logger.info("KB chunk-first search %r: cache hit merged=%d", query[:60], len(cached))
        return list(cached)

    existing = _search_inflight.get(key)
    if existing is not None:
        return list(await existing)

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
    _search_inflight[key] = fut
    try:
        merged = await _search_hits_chunk_first_uncached(
            query, limit=limit, filters=filters, threshold=threshold
        )
        if len(_search_hit_cache) >= _SEARCH_CACHE_MAX:
            _search_hit_cache.pop(next(iter(_search_hit_cache)))
        _search_hit_cache[key] = merged
        fut.set_result(merged)
        return list(merged)
    except Exception as exc:
        fut.set_exception(exc)
        raise
    finally:
        _search_inflight.pop(key, None)


async def _search_hits_chunk_first_uncached(
    query: str,
    *,
    limit: int,
    filters: dict[str, Any] | None,
    threshold: float,
) -> list[dict[str, Any]]:
    from app.services import supermemory

    active_filters = filters or supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS
    chunk_limit = max(limit * 2, 16)
    memory_limit = max(limit // 2, 4)

    async def _chunks() -> list[dict[str, Any]]:
        try:
            return await supermemory.search_document_chunks(
                query=query,
                limit=chunk_limit,
                filters=active_filters,
                threshold=threshold,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("KB chunk search failed for %r: %s", query[:60], exc)
            return []

    async def _hybrid() -> list[dict[str, Any]]:
        try:
            return await supermemory.search_hybrid(
                query=query,
                limit=memory_limit,
                include_full_docs=True,
                filters=active_filters,
                threshold=threshold,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("KB hybrid search failed for %r: %s", query[:60], exc)
            return []

    chunk_hits, memory_hits = await asyncio.gather(_chunks(), _hybrid())
    chunk_hits = [h for h in chunk_hits if supermemory.is_knowledge_base_hit(h)]
    memory_hits = [h for h in memory_hits if supermemory.is_knowledge_base_hit(h)]
    merged = supermemory.merge_chunk_first_hits(memory_hits, chunk_hits)
    logger.info(
        "KB chunk-first search %r: chunks=%d memories=%d merged=%d",
        query[:60],
        len(chunk_hits),
        len(memory_hits),
        len(merged),
    )
    return merged


async def retrieve_for_question(
    question: str,
    *,
    limit: int = 8,
    max_chars: int = 80_000,
    category: str | None = None,
    threshold: float = 0.35,
    fallback_threshold: float = 0.22,
) -> tuple[str, list[str], list[str]]:
    """Search Supermemory with the user question; pack full docs when possible.

    Returns (context, source_labels, queries_used).
    """
    import asyncio

    from app.services import supermemory

    filters: dict[str, Any] | None = None
    if category:
        filters = {
            "AND": [
                *supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
                {"key": "category", "value": category},
            ]
        }

    queries = expand_kb_queries(question)
    logger.info(
        "KB RAG query %r → %d search(es) head=%r",
        question[:80],
        len(queries),
        (queries[0][:80] if queries else ""),
    )

    async def _search_one(query: str, thresh: float) -> list[dict[str, Any]]:
        try:
            return await _search_hits_chunk_first(
                query,
                limit=max(limit, 12),
                filters=filters,
                threshold=thresh,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("KB RAG search failed for %r: %s", query[:60], exc)
            return []

    async def _collect(thresh: float) -> list[dict[str, Any]]:
        groups = await asyncio.gather(*[_search_one(q, thresh) for q in queries])
        merged_local: list[dict[str, Any]] = []
        seen_local: set[tuple[str, bool]] = set()
        for hits in groups:
            for hit in hits:
                doc_key = supermemory.document_dedupe_key(hit) or str(hit.get("id") or id(hit))
                # Include the hit kind in the key so a memory and a chunk from the
                # same document don't collide a second time (see merge_chunk_first_hits,
                # which already keeps both) — otherwise the cross-query merge here
                # would re-discard the memory that Task 1 stopped dropping upstream.
                key = (doc_key, supermemory.is_memory_hit(hit))
                if key in seen_local:
                    continue
                seen_local.add(key)
                merged_local.append(hit)
        return merged_local

    merged = await _collect(threshold)
    if not merged and fallback_threshold < threshold:
        logger.info(
            "KB RAG empty at threshold=%.2f — retrying at %.2f",
            threshold,
            fallback_threshold,
        )
        merged = await _collect(fallback_threshold)

    full_ranked = rank_hits_for_question(merged, question)
    ranked = full_ranked[: max(limit, 6)]
    # Chunks keep their positions from rank_hits_for_question — never reordered
    # above chunks. Then TOP UP to MEMORY_FLOOR memory hits from the ones the cut
    # dropped, appended after the chunks. A plain "did any memory survive?" check
    # is not enough: one memory from an adjacent document (e.g. a pricing guide)
    # would satisfy it while the memory actually holding the answer (the rate
    # card) stays cut. Fact-level answers usually need several memories, not one.
    # Counting memories is not enough: three memories from an adjacent document
    # (a pricing guide) would satisfy a numeric floor while the best-matching
    # memory — the rate card holding the actual answer — stays below the cut.
    # Take the top MEMORY_FLOOR memories by rank and guarantee those specific
    # hits are present, appended after the chunks.
    top_memories = [hit for hit in full_ranked if supermemory.is_memory_hit(hit)][
        :MEMORY_FLOOR
    ]
    already = {id(hit) for hit in ranked}
    ranked = ranked + [hit for hit in top_memories if id(hit) not in already]

    parts: list[str] = []
    sources: list[str] = []
    total = 0
    # Give each top hit enough room for a full small/medium case-study PDF
    per_doc = max(12_000, max_chars // max(min(len(ranked), 4), 1))
    # Reserve a slice of the total budget that only memory hits may spend, so
    # long chunks packed earlier in the loop can't exhaust max_chars before a
    # short memory fact is ever reached.
    memory_reserve = MEMORY_FLOOR * MEMORY_BUDGET_CHARS
    chunk_max_chars = max(max_chars - memory_reserve, 0)

    for hit in ranked:
        is_memory = supermemory.is_memory_hit(hit)
        if is_memory:
            remaining = max_chars - total
            if remaining <= 0:
                continue
            budget = min(MEMORY_BUDGET_CHARS, remaining)
        else:
            remaining = chunk_max_chars - total
            if remaining < 400:
                # Skip this chunk, do NOT break: memories are appended after the
                # chunks, and breaking here would strand every one of them once
                # the chunk budget ran out — which is exactly how the rate card
                # kept missing from rate answers.
                continue
            budget = min(per_doc, remaining)
        full = ""
        try:
            full = await supermemory.resolve_hit_document_content(hit)
        except supermemory.SupermemoryError:
            full = ""
        block = pack_hit_context(
            hit,
            full_document=full,
            question=question,
            max_chars=budget,
        )
        if not block.strip():
            continue
        # A one-line memory fact is short enough that the term-overlap ratio is
        # noise, and a memory that made it into `ranked` already matched the
        # question — so it skips the overlap filter that chunks still go through.
        if not is_memory:
            terms = _question_terms(question)
            if terms and _term_overlap(block, terms) < 0.15:
                continue
        label = _hit_label(hit) or "document"
        parts.append(block)
        if label not in sources:
            sources.append(label)
        total += len(block)

    context = "\n\n".join(parts).strip()
    return context or "(No matching knowledge-base content.)", sources, queries
