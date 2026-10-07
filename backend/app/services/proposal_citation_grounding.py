"""Post-hoc claim → evidenceCorpus citation map (no LLM [E#] required).

After manuscript prose is stable, map sentences to existing EvidenceItem ids by
lexical overlap / containment. UI renders badges from citationMap; export
never sees the map (and still strips any inline [E#]).

Budget / Cost sections are special: dollar figures come from the Pricing Book
plan (catalog SKU or custom build), not from RFP/KB narrative retrieval. Those
amounts are grounded mechanically to Pricing Book evidence items so review
badges show where each number came from.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from app.models.proposal import (
    CitationGrounding,
    EvidenceItem,
    ProposalBudget,
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
_PRICING_CHUNK_PREFIX = "pricing:"
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


def _next_evidence_num(corpus: list[EvidenceItem]) -> int:
    n = 0
    for item in corpus:
        m = re.match(r"E(\d+)$", (item.id or "").strip(), flags=re.I)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def _money_spans_for_amount(content: str, amount: float) -> list[str]:
    """Exact substrings in content that display this amount (table + prose forms)."""
    from app.services.pricing_plan_engine import usd

    token = re.escape(usd(amount))
    pat = re.compile(rf"{token}(?:\s*/\s*month|\s*/\s*event|\s+a\s+month)?")
    return list(dict.fromkeys(m.group(0) for m in pat.finditer(content or "")))


def _is_budget_section(section: ProposalSection) -> bool:
    try:
        from app.services.proposal_budget_content import budget_section_score

        return budget_section_score(section.title or "") > 0
    except Exception:  # noqa: BLE001
        title = (section.title or "").casefold()
        return any(k in title for k in ("cost", "budget", "pricing", "fee", "investment"))


def build_pricing_source_evidence(
    budget: ProposalBudget,
    *,
    section_id: str = "",
    start_index: int = 1,
) -> tuple[list[EvidenceItem], dict[str, str]]:
    """EvidenceItems for each priced figure + map chunk_key → E#.

    Catalog tasks cite Pricing Book SKU + list price. Custom builds cite that the
    sell price was computed from the Pricing Book / Pricing Internal plan (not a
    catalog SKU). Rates cite the blended rate setting.
    """
    plan = budget.pricing_plan or {}
    snap = plan.get("kb_snapshot") or {}
    if not plan or not snap:
        return [], {}

    from app.services.pricing_kb import PricingBook
    from app.services.pricing_plan_engine import SUFFIX, compute, term_value, usd

    try:
        book = PricingBook.from_snapshot(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pricing citation: snapshot rebuild failed: %s", exc)
        return [], {}

    c = compute(plan, book)
    version = book.version or snap.get("version") or "v2"
    items: list[EvidenceItem] = []
    key_to_id: dict[str, str] = {}
    n = start_index
    sids = [section_id] if section_id else []

    def _add(chunk_key: str, source: str, excerpt: str) -> str:
        nonlocal n
        eid = f"E{n}"
        n += 1
        key_to_id[chunk_key] = eid
        items.append(
            EvidenceItem(
                id=eid,
                source=source,
                excerpt=excerpt[:2000],
                sectionIds=list(sids),
                chunkKey=chunk_key,
            )
        )
        return eid

    for t in plan.get("tasks") or []:
        tid = str(t.get("task_id") or "").strip()
        if not tid:
            continue
        amt = c["amounts"].get(tid)
        if amt is None:
            continue
        billing = t.get("billing") or "one_time"
        display = f"{usd(amt)}{SUFFIX.get(billing, '')}"
        code = (t.get("catalog_code") or "").strip()
        row = (c.get("rows") or {}).get(tid) or {}
        kind = row.get("kind") or ("catalog" if code else "custom")

        if kind == "catalog" and code and code in book.catalog:
            cat = book.catalog[code]
            chunk = f"{_PRICING_CHUNK_PREFIX}catalog:{code}:{tid}"
            _add(
                chunk,
                f"Pricing Book {version} · catalog {code}",
                (
                    f"Pricing Book {version} catalog {code} — {cat.item}: "
                    f"{usd(cat.price)} {cat.unit}. "
                    f"{(cat.description or '').strip()} "
                    f"Plan task {tid} bills {display}."
                ).strip(),
            )
        else:
            basis = ""
            build = t.get("build")
            if isinstance(build, dict):
                basis = str(build.get("basis") or "").strip()
            chunk = f"{_PRICING_CHUNK_PREFIX}build:{tid}"
            _add(
                chunk,
                f"Pricing Book {version} · custom build",
                (
                    f"Pricing Book {version}: task {tid} is not a catalog SKU — "
                    f"sell price {display} from the pricing plan engine "
                    f"(Pricing Internal roles/POs + book settings). "
                    f"{('Basis: ' + basis) if basis else ''}"
                ).strip(),
            )

    total = term_value(c)
    if total:
        _add(
            f"{_PRICING_CHUNK_PREFIX}total",
            f"Pricing Book {version} · term total",
            (
                f"Pricing Book {version} plan term value {usd(total)} — "
                f"sum of priced tasks over the engagement term "
                f"({c.get('months', 12)} months for monthly lines)."
            ),
        )

    rate = c.get("rate")
    if rate is not None and (plan.get("hourly_roles") or []):
        _add(
            f"{_PRICING_CHUNK_PREFIX}rate",
            f"Pricing Book {version} · blended rate",
            (
                f"Pricing Book {version} blended rate {usd(rate)} / hour "
                f"(same rate for every role when the RFP asks for rates)."
            ),
        )

    return items, key_to_id


def ground_budget_pricing_citations(
    content: str,
    budget: ProposalBudget,
    *,
    evidence: list[EvidenceItem],
) -> list[CitationGrounding]:
    """Map each displayed dollar figure in the Cost section to its pricing evidence."""
    if not content.strip() or not evidence:
        return []

    plan = budget.pricing_plan or {}
    snap = plan.get("kb_snapshot") or {}
    if not plan or not snap:
        return []

    from app.services.pricing_kb import PricingBook
    from app.services.pricing_plan_engine import compute, term_value

    try:
        book = PricingBook.from_snapshot(snap)
    except Exception:  # noqa: BLE001
        return []

    c = compute(plan, book)
    by_key = {
        (e.chunk_key or "").strip(): e
        for e in evidence
        if (e.chunk_key or "").startswith(_PRICING_CHUNK_PREFIX)
    }
    out: list[CitationGrounding] = []
    seen: set[tuple[str, str]] = set()

    def _cite(amount: float, chunk_key: str) -> None:
        item = by_key.get(chunk_key)
        if not item or amount is None:
            return
        eid = (item.id or "").strip().upper()
        if not eid:
            return
        for span in _money_spans_for_amount(content, float(amount)):
            key = (span, eid)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                CitationGrounding(
                    text=span,
                    evidence_ids=[eid],
                    method="verbatim",
                )
            )

    for t in plan.get("tasks") or []:
        tid = str(t.get("task_id") or "").strip()
        amt = c["amounts"].get(tid)
        if amt is None or not tid:
            continue
        code = (t.get("catalog_code") or "").strip()
        row = (c.get("rows") or {}).get(tid) or {}
        kind = row.get("kind") or ("catalog" if code else "custom")
        chunk = (
            f"{_PRICING_CHUNK_PREFIX}catalog:{code}:{tid}"
            if kind == "catalog" and code
            else f"{_PRICING_CHUNK_PREFIX}build:{tid}"
        )
        _cite(float(amt), chunk)

    total = term_value(c)
    if total:
        _cite(float(total), f"{_PRICING_CHUNK_PREFIX}total")

    rate = c.get("rate")
    if rate is not None and (plan.get("hourly_roles") or []):
        _cite(float(rate), f"{_PRICING_CHUNK_PREFIX}rate")

    return out


def merge_pricing_evidence_into_corpus(
    corpus: list[EvidenceItem],
    pricing_items: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Replace prior pricing:* stubs, append fresh ones with new E# ids."""
    kept = [
        e
        for e in corpus
        if not (e.chunk_key or "").startswith(_PRICING_CHUNK_PREFIX)
    ]
    n = _next_evidence_num(kept)
    out = list(kept)
    for item in pricing_items:
        eid = f"E{n}"
        n += 1
        out.append(item.model_copy(update={"id": eid}))
    return out


def attach_citation_maps_to_draft(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    *,
    label: str = "citation-map",
) -> tuple[ProposalDraft, list[str]]:
    """Stamp citationMap (+ kbRefs union) on every section. Does not rewrite prose.

    When research.budget is a pricing_plan, Cost sections get mechanical citations
    to Pricing Book catalog/build sources (and those evidence rows are merged into
    research.evidence_corpus in place for badge tooltips).
    """
    corpus = list((research.evidence_corpus if research else None) or [])
    if not draft.sections:
        return draft, []

    logs: list[str] = []
    budget = research.budget if research else None
    budget_sid = ""
    pricing_evidence: list[EvidenceItem] = []

    if budget and budget.pricing_plan:
        from app.services.proposal_budget_content import find_budget_section_index

        bidx = find_budget_section_index(draft.sections)
        if bidx is not None:
            budget_sid = draft.sections[bidx].id or ""
        pricing_evidence, _ = build_pricing_source_evidence(
            budget,
            section_id=budget_sid,
            start_index=_next_evidence_num(corpus),
        )
        if pricing_evidence:
            corpus = merge_pricing_evidence_into_corpus(corpus, pricing_evidence)
            # Re-read items with final ids for grounding.
            pricing_evidence = [
                e
                for e in corpus
                if (e.chunk_key or "").startswith(_PRICING_CHUNK_PREFIX)
            ]
            if research is not None:
                research.evidence_corpus = corpus
            logs.append(
                f"{label}: added {len(pricing_evidence)} Pricing Book source citation(s)"
            )

    if not corpus:
        return draft, logs

    new_sections: list[ProposalSection] = []
    total = 0
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            new_sections.append(
                section.model_copy(update={"citation_map": [], "kb_refs": []})
            )
            continue

        if (
            budget
            and budget.pricing_plan
            and pricing_evidence
            and _is_budget_section(section)
        ):
            # Do not lexical-match won-proposal rate cards onto Cost dollars.
            cmap = ground_budget_pricing_citations(
                content, budget, evidence=pricing_evidence
            )
        else:
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
    logs.append(
        f"{label}: grounded {total} claim citation(s) across {len(new_sections)} section(s)"
    )
    logger.info("%s", logs[-1])
    return draft.model_copy(update={"sections": new_sections}), logs
