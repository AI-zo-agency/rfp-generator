"""Post-hoc claim → evidenceCorpus citation map (no LLM [E#] required).

After manuscript prose is stable, map sentences to existing EvidenceItem ids by
lexical overlap / containment. UI renders badges from citationMap; export
never sees the map (and still strips any inline [E#]).
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from app.models.proposal import (
    CitationGrounding,
    EvidenceItem,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)

logger = logging.getLogger(__name__)

# ponytail: lexical overlap only — swap in embeddings if false-positive rate climbs.
_MIN_CLAIM_CHARS = 28
_MAX_CITATIONS_PER_CLAIM = 2
_SCORE_FLOOR = 0.42
_MAX_CLAIMS_PER_SECTION = 80

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?|\b\d{1,3}(?:\.\d+)?\s*%")
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    {
        "the",
        "and",
        "a",
        "an",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "our",
        "we",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "or",
        "at",
        "from",
        "this",
        "that",
        "will",
        "can",
        "has",
        "have",
        "zö",
        "zo",
        "agency",
        "their",
        "its",
        "into",
        "than",
        "also",
        "which",
        "who",
        "whom",
    }
)
_GENERIC_LEAD = re.compile(
    r"^(?:we look forward|thank you|please find|as requested|in summary|"
    r"in conclusion|this proposal|the following)\b",
    re.I,
)
_SKIP_LINE = re.compile(
    r"^\s*(?:#{1,6}\s|[-*+]\s*\[[ xX]\]|"
    r"\[(?:MANUAL\s+FILL|VERIFY|DESIGNER\s+NOTE|FLAG|E\d+))",
    re.I,
)
_INLINE_CITE = re.compile(r"\[E\d+(?:\s*[,;]\s*E\d+)*\]", re.I)


def _normalize(text: str) -> str:
    t = (text or "").casefold()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _content_tokens(text: str) -> set[str]:
    return {m.group(0) for m in _TOKEN_RE.finditer(_normalize(text)) if m.group(0) not in _STOP}


def _distinctive_anchors(text: str) -> set[str]:
    return {m.group(0) for m in _YEAR_RE.finditer(text or "")} | {
        m.group(0).replace(" ", "") for m in _MONEY_RE.finditer(text or "")
    }


def _split_sentences(text: str) -> list[str]:
    """Lightweight sentence split (same idea as claim_validator)."""
    if not text:
        return []
    out: list[str] = []
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        buf += ch
        if ch in ".!?":
            is_cents = (
                ch == "."
                and i + 1 < len(text)
                and text[i + 1].isdigit()
                and len(buf) >= 2
                and buf[-2].isdigit()
            )
            if not is_cents:
                piece = buf.strip()
                if piece:
                    out.append(piece)
                buf = ""
        i += 1
    trailing = buf.strip()
    if trailing:
        out.append(trailing)
    return out


def _iter_claim_spans(content: str) -> list[str]:
    """Claims from prose lines; skip headings, tags, empty table chrome."""
    claims: list[str] = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("|") or set(line) <= set("-|: "):
            continue
        if _SKIP_LINE.match(line):
            continue
        # Drop markdown heading markers for matching; keep body.
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^\*\*(.+?)\*\*:?\s*$", r"\1", line)
        for sent in _split_sentences(line):
            cleaned = _INLINE_CITE.sub("", sent).strip()
            cleaned = re.sub(r"\s{2,}", " ", cleaned)
            if len(cleaned) < _MIN_CLAIM_CHARS:
                continue
            if _GENERIC_LEAD.match(cleaned):
                continue
            if cleaned.startswith("[") and cleaned.endswith("]"):
                continue
            claims.append(cleaned)
            if len(claims) >= _MAX_CLAIMS_PER_SECTION:
                return claims
    return claims


def score_claim_against_excerpt(claim: str, excerpt: str) -> float:
    """0–1 support score. High = excerpt grounds the claim."""
    cn = _normalize(claim)
    en = _normalize(excerpt)
    if len(cn) < _MIN_CLAIM_CHARS or len(en) < 20:
        return 0.0
    if cn in en:
        return 0.98
    if len(en) >= 40 and en in cn:
        return 0.9

    ct = _content_tokens(claim)
    et = _content_tokens(excerpt)
    if len(ct) < 4:
        return 0.0
    shared = ct & et
    anchors = _distinctive_anchors(claim) & _distinctive_anchors(excerpt)
    if len(shared) < 3 and not anchors:
        return 0.0

    jacc = len(shared) / max(1, len(ct | et))
    seq = SequenceMatcher(None, cn[:500], en[:900]).ratio()
    score = 0.55 * jacc + 0.45 * seq
    if anchors:
        score += 0.12
    # Prefer denser shared content words.
    if len(shared) >= 6:
        score += 0.05
    return min(score, 1.0)


def _corpus_for_section(
    corpus: list[EvidenceItem], section_id: str
) -> list[EvidenceItem]:
    tagged = [e for e in corpus if section_id and section_id in (e.section_ids or [])]
    pool = tagged if tagged else list(corpus)
    # Prefer real E# evidence items; skip empty excerpts.
    return [e for e in pool if (e.id or "").strip() and (e.excerpt or "").strip()]


def ground_section_citations(
    content: str,
    corpus: list[EvidenceItem],
    *,
    section_id: str = "",
) -> list[CitationGrounding]:
    """Build citation map for one section body against the evidence corpus."""
    if not content.strip() or not corpus:
        return []

    pool = _corpus_for_section(corpus, section_id)
    if not pool:
        return []

    # Precompute nothing expensive — corpus is tens/hundreds.
    out: list[CitationGrounding] = []
    seen_claim_keys: set[str] = set()

    for claim in _iter_claim_spans(content):
        key = _normalize(claim)[:160]
        if key in seen_claim_keys:
            continue
        seen_claim_keys.add(key)

        ranked: list[tuple[float, EvidenceItem]] = []
        for item in pool:
            s = score_claim_against_excerpt(claim, item.excerpt or "")
            if s >= _SCORE_FLOOR:
                ranked.append((s, item))
        if not ranked:
            continue
        ranked.sort(key=lambda t: t[0], reverse=True)
        ids: list[str] = []
        method = "overlap"
        for s, item in ranked[:_MAX_CITATIONS_PER_CLAIM]:
            eid = (item.id or "").strip().upper()
            if not eid or eid in ids:
                continue
            ids.append(eid)
            if s >= 0.9:
                method = "verbatim"
        if ids:
            out.append(
                CitationGrounding(
                    text=claim,
                    evidence_ids=ids,
                    method=method,  # type: ignore[arg-type]
                )
            )
    return out


def attach_citation_maps_to_draft(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    *,
    label: str = "citation-map",
) -> tuple[ProposalDraft, list[str]]:
    """Stamp citationMap (+ kbRefs union) on every section. Does not rewrite prose."""
    corpus = list((research.evidence_corpus if research else None) or [])
    if not corpus or not draft.sections:
        return draft, []

    logs: list[str] = []
    new_sections: list[ProposalSection] = []
    total = 0
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            new_sections.append(
                section.model_copy(update={"citation_map": [], "kb_refs": []})
            )
            continue
        cmap = ground_section_citations(
            content, corpus, section_id=section.id or ""
        )
        refs = sorted({eid for row in cmap for eid in row.evidence_ids})
        # Preserve any E# already inline in prose.
        for m in _INLINE_CITE.finditer(content):
            for eid in re.findall(r"E\d+", m.group(0), flags=re.I):
                u = eid.upper()
                if u not in refs:
                    refs.append(u)
        refs = sorted(set(refs), key=lambda x: int(re.sub(r"\D", "", x) or 0))
        total += len(cmap)
        new_sections.append(
            section.model_copy(update={"citation_map": cmap, "kb_refs": refs})
        )
    logs.append(f"{label}: grounded {total} claim citation(s) across {len(new_sections)} section(s)")
    logger.info("%s", logs[-1])
    return draft.model_copy(update={"sections": new_sections}), logs
