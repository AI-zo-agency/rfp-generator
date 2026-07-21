"""KB RAG helpers: thorough Supermemory fetch + context packing.

Searches v4 hybrid (memories) and documents (chunks). Memories win when present;
chunks fill docs that have no memory. Full documents are packed when they fit.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

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


def expand_kb_queries(question: str, *, max_queries: int = 3) -> list[str]:
    """Search queries = the user question only (no static topic expansions)."""
    q = (question or "").strip()
    if not q:
        return []
    return [q][:max_queries]


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
    if "filingguide" in n or "claude_knowledge" in n or "00_guide" in n:
        score -= 1.5
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
    for key in ("content", "chunk", "memory", "text", "summary"):
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
    terms = _question_terms(question)
    q_cf = (question or "").casefold()
    ask_about_rfp = bool(re.search(r"\brfp\b|solicitation", q_cf))
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
    label = _hit_label(hit) or "document"
    header = f"### {label}"
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
    from app.services.proposal_knowledge_base_tools import _search_hits_all_modes

    filters: dict[str, Any] | None = None
    if category:
        filters = {
            "AND": [
                *supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
                {"key": "category", "value": category},
            ]
        }

    queries = expand_kb_queries(question)
    logger.info("KB RAG query %r", question[:80])

    async def _search_one(query: str, thresh: float) -> list[dict[str, Any]]:
        try:
            return await _search_hits_all_modes(
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
        seen_local: set[str] = set()
        for hits in groups:
            for hit in hits:
                key = supermemory.document_dedupe_key(hit) or str(hit.get("id") or id(hit))
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

    ranked = rank_hits_for_question(merged, question)[: max(limit, 6)]

    parts: list[str] = []
    sources: list[str] = []
    total = 0
    # Give each top hit enough room for a full small/medium case-study PDF
    per_doc = max(12_000, max_chars // max(min(len(ranked), 4), 1))

    for hit in ranked:
        remaining = max_chars - total
        if remaining < 400:
            break
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
