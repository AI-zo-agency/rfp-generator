"""Post-hoc claim → evidenceCorpus citation map (no LLM [E#] required).

After manuscript prose is stable, map sentences and table cells to EvidenceItem
ids by lexical overlap / containment. UI renders badges from citationMap; export
never sees the map (and still strips any inline [E#]).

Two source pools, kept separate so one cannot hide the other:
- Knowledge-base excerpts already on the section (or the shared corpus when the
  section has no tagged packet).
- RFP requirements stored on the research cache for that section. A plan
  sentence can carry both a KB id and an RFP id.

Budget / Cost sections are special: dollar figures come from the Pricing Book
plan (catalog SKU or custom build), not from RFP/KB narrative retrieval. Those
amounts are grounded mechanically to Pricing Book evidence items so review
badges show where each number came from. Table cells in that section still
match the Pricing Book excerpts, not won-proposal rate cards.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

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
_RFP_CHUNK_PREFIX = "rfp-req:"
_MIN_TABLE_CELL_CHARS = 16
_MAX_MERGED_IDS = 4
_TABLE_SEP_CELL = re.compile(r"^:?-{3,}:?$")
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


def _is_table_separator(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(not c or _TABLE_SEP_CELL.match(c) for c in cells)


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]


def _accept_claim(claims: list[str], sent: str, *, min_chars: int) -> None:
    """Keep a verbatim span long enough to cite. ``sent`` must occur in the body."""
    if _INLINE_CITE.fullmatch(sent.strip()) and claims:
        claims[-1] = f"{claims[-1]} {sent.strip()}"
        return
    cleaned = _INLINE_CITE.sub("", sent).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if len(cleaned) < min_chars:
        return
    if _GENERIC_LEAD.match(cleaned):
        return
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return
    # Keep [E#] on the span so provenance can be read after a voice rewrite.
    claims.append(re.sub(r"\s{2,}", " ", sent).strip())


def _iter_claim_spans(content: str) -> list[str]:
    """Claims from prose sentences and markdown table cells.

    Separator rows are chrome. A cell is cited from its own text so the review
    badge lands inside the cell instead of after the pipe row.
    """
    claims: list[str] = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or set(line) <= set("-|: "):
            continue
        if line.startswith("|"):
            if _is_table_separator(line):
                continue
            for cell in _table_cells(line):
                if _SKIP_LINE.match(cell):
                    continue
                for sent in _split_sentences(cell):
                    _accept_claim(claims, sent, min_chars=_MIN_TABLE_CELL_CHARS)
                    if len(claims) >= _MAX_CLAIMS_PER_SECTION:
                        return claims
            continue
        if _SKIP_LINE.match(line):
            continue
        # Drop markdown heading markers for matching; keep body.
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^\*\*(.+?)\*\*:?\s*$", r"\1", line)
        for sent in _split_sentences(line):
            _accept_claim(claims, sent, min_chars=_MIN_CLAIM_CHARS)
            if len(claims) >= _MAX_CLAIMS_PER_SECTION:
                return claims
    return claims


def score_claim_against_excerpt(
    claim: str,
    excerpt: str,
    *,
    min_chars: int = _MIN_CLAIM_CHARS,
) -> float:
    """0–1 support score. High = excerpt grounds the claim."""
    cn = _normalize(claim)
    en = _normalize(excerpt)
    if len(cn) < min_chars or len(en) < 20:
        return 0.0
    ct = _content_tokens(claim)
    anchors = _distinctive_anchors(claim) & _distinctive_anchors(excerpt)
    # A short label that merely occurs inside an excerpt is not support.
    # "Creative Director" sits in someone else's resume. A real claim carries
    # enough of its own words, or a year or amount that is actually there.
    if cn in en or (len(en) >= 40 and en in cn):
        if len(ct) < 4 and not anchors:
            return 0.0
        if cn in en:
            return 0.98
        return 0.9

    et = _content_tokens(excerpt)
    if len(ct) < 4:
        return 0.0
    shared = ct & et
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


def is_citable_evidence(item: EvidenceItem) -> bool:
    """True for a real E# excerpt. Flags, proof-point labels, and memory summaries are not sources."""
    eid = (item.id or "").strip()
    if not re.fullmatch(r"E\d+", eid, flags=re.I):
        return False
    if (item.source or "") == "evidence_trust_gate":
        return False
    if (item.chunk_key or "").startswith("evidence-trust-gap"):
        return False
    blob = f"{item.source or ''}\n{item.excerpt or ''}".casefold()
    if "do not cite" in blob or "memory summary" in blob:
        return False
    return bool((item.excerpt or "").strip())


def _inline_evidence_ids(text: str) -> list[str]:
    found: list[str] = []
    for match in _INLINE_CITE.finditer(text or ""):
        for eid in re.findall(r"E\d+", match.group(0), flags=re.I):
            upper = eid.upper()
            if upper not in found:
                found.append(upper)
    return found


def strip_uncitable_markers(text: str, allowed_ids: set[str]) -> str:
    """Drop [E#] markers whose ids were not in this section's evidence packet."""

    def _repl(match: re.Match[str]) -> str:
        ids = _inline_evidence_ids(match.group(0))
        keep = [eid for eid in ids if eid in allowed_ids]
        if not keep:
            return ""
        if keep == ids:
            return match.group(0)
        return f"[{keep[0]}]" if len(keep) == 1 else f"[{', '.join(keep)}]"

    return _INLINE_CITE.sub(_repl, text or "")


def _corpus_for_section(
    corpus: list[EvidenceItem], section_id: str
) -> list[EvidenceItem]:
    tagged = [e for e in corpus if section_id and section_id in (e.section_ids or [])]
    pool = tagged if tagged else list(corpus)
    # Section-tagged rows win even when none of them are citable, so a flag
    # packet cannot fall through to another section's excerpt.
    if tagged:
        return [e for e in tagged if is_citable_evidence(e)]
    return [e for e in pool if is_citable_evidence(e)]


def _min_chars_for(plain: str) -> int:
    return _MIN_TABLE_CELL_CHARS if len(plain) < _MIN_CLAIM_CHARS else _MIN_CLAIM_CHARS


def _item_by_id(pool: list[EvidenceItem], eid: str) -> EvidenceItem | None:
    want = (eid or "").strip().upper()
    for item in pool:
        if (item.id or "").strip().upper() == want:
            return item
    return None


def _excerpt_supports_claim(
    plain: str,
    item: EvidenceItem | None,
    *,
    min_chars: int,
) -> bool:
    if item is None or not is_citable_evidence(item):
        return False
    return score_claim_against_excerpt(plain, item.excerpt or "", min_chars=min_chars) >= _SCORE_FLOOR


def drop_unsupported_inline_markers(
    content: str,
    corpus: list[EvidenceItem],
    *,
    section_id: str = "",
) -> str:
    """Remove [E#] markers whose excerpt does not support that claim."""
    if not content.strip():
        return content
    pool = _corpus_for_section(corpus, section_id)
    updated = content
    for claim in _iter_claim_spans(content):
        inline = _inline_evidence_ids(claim)
        if not inline:
            continue
        plain = re.sub(r"\s{2,}", " ", _INLINE_CITE.sub("", claim).strip())
        min_chars = _min_chars_for(plain)
        keep = [
            eid
            for eid in inline
            if _excerpt_supports_claim(plain, _item_by_id(pool, eid), min_chars=min_chars)
        ]
        if keep == inline:
            continue
        if keep:
            replacement = f"[{', '.join(keep)}]" if len(keep) > 1 else f"[{keep[0]}]"
            new_claim = _INLINE_CITE.sub(replacement, claim, count=1)
        else:
            new_claim = re.sub(r"\s{2,}", " ", _INLINE_CITE.sub("", claim)).strip()
        if claim in updated and new_claim != claim:
            updated = updated.replace(claim, new_claim, 1)
            logger.info(
                "citation dropped unsupported marker section=%s ids=%s",
                section_id,
                ",".join(eid for eid in inline if eid not in keep),
            )
    return updated


def ground_section_citations(
    content: str,
    corpus: list[EvidenceItem],
    *,
    section_id: str = "",
) -> list[CitationGrounding]:
    """Build citation map for one section body against the evidence corpus.

    An inline [E#] is a candidate. It is kept only when that exact excerpt
    supports the claim. Overlap is the path for a sentence with no marker.
    """
    if not content.strip() or not corpus:
        return []

    pool = _corpus_for_section(corpus, section_id)
    if not pool:
        return []
    allowed = {(e.id or "").strip().upper() for e in pool}

    # Precompute nothing expensive — corpus is tens/hundreds.
    out: list[CitationGrounding] = []
    seen_claim_keys: set[str] = set()

    for claim in _iter_claim_spans(content):
        plain = _INLINE_CITE.sub("", claim).strip()
        plain = re.sub(r"\s{2,}", " ", plain)
        key = _normalize(plain)[:160]
        if key in seen_claim_keys:
            continue
        seen_claim_keys.add(key)

        inline = [eid for eid in _inline_evidence_ids(claim) if eid in allowed]
        if inline:
            # The drafter named a row. If that row does not support the claim,
            # do not shop the rest of the packet for a different document.
            supported = [
                eid
                for eid in inline
                if _excerpt_supports_claim(plain, _item_by_id(pool, eid), min_chars=_min_chars_for(plain))
            ]
            if supported:
                out.append(
                    CitationGrounding(
                        text=plain,
                        evidence_ids=supported,
                        method="inline_provenance",
                    )
                )
            continue

        ranked: list[tuple[float, EvidenceItem]] = []
        min_chars = _min_chars_for(plain)
        for item in pool:
            s = score_claim_against_excerpt(plain, item.excerpt or "", min_chars=min_chars)
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
                    text=plain,
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


def merge_prefixed_evidence_into_corpus(
    corpus: list[EvidenceItem],
    items: list[EvidenceItem],
    prefix: str,
) -> list[EvidenceItem]:
    """Replace prior ``prefix`` stubs, append fresh ones with new E# ids."""
    kept = [e for e in corpus if not (e.chunk_key or "").startswith(prefix)]
    n = _next_evidence_num(kept)
    out = list(kept)
    for item in items:
        eid = f"E{n}"
        n += 1
        out.append(item.model_copy(update={"id": eid}))
    return out


def merge_pricing_evidence_into_corpus(
    corpus: list[EvidenceItem],
    pricing_items: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Replace prior pricing:* stubs, append fresh ones with new E# ids."""
    return merge_prefixed_evidence_into_corpus(
        corpus, pricing_items, _PRICING_CHUNK_PREFIX
    )


def _requirement_texts_for_section(
    research: ProposalResearchCache | None,
    section: ProposalSection,
) -> list[str]:
    """RFP requirement strings this section was written to answer."""
    if research is None:
        return []
    sid = (section.id or "").strip()
    title_key = (section.title or "").strip().casefold()
    texts: list[str] = []
    title_fallback: list[str] = []
    for mapped in research.rfp_sections or []:
        reqs = [str(r) for r in (mapped.requirements or []) if str(r).strip()]
        if sid and (mapped.id or "").strip() == sid:
            texts.extend(reqs)
        elif title_key and (mapped.title or "").strip().casefold() == title_key:
            title_fallback.extend(reqs)
    if not texts:
        texts.extend(title_fallback)
    ledger = research.requirement_ledger
    if ledger is not None and sid:
        for req in ledger.requirements or []:
            if sid in (req.satisfied_by or []):
                texts.append(req.text or "")
    seen: set[str] = set()
    clean: list[str] = []
    for raw in texts:
        text = re.sub(r"\s+", " ", (raw or "").strip())
        if len(text) < 20:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        clean.append(text[:2000])
    return clean


def _verbatim_rfp_span(requirement: str, rfp_text: str) -> str:
    """Return the requirement only when it is a span of the solicitation.

    Planner labels ("Verified public-sector clients") are not excerpts.
    """
    text = re.sub(r"\s+", " ", (requirement or "").strip())
    body = re.sub(r"\s+", " ", (rfp_text or "").strip())
    if len(text) < 20 or not body:
        return ""
    if text.casefold() not in body.casefold():
        return ""
    return text[:2000]


def build_rfp_requirement_evidence(
    sections: list[ProposalSection],
    research: ProposalResearchCache | None,
    *,
    start_index: int = 1,
    rfp_text: str = "",
) -> list[EvidenceItem]:
    """One evidence row per requirement that is quoted from the solicitation."""
    items: list[EvidenceItem] = []
    n = start_index
    for section in sections:
        sid = (section.id or "").strip()
        title = (section.title or sid or "section").strip()
        index = 0
        for raw in _requirement_texts_for_section(research, section):
            text = _verbatim_rfp_span(raw, rfp_text)
            if not text:
                logger.info(
                    "rfp citation skipped not-verbatim section=%s text=%s",
                    sid,
                    raw[:80],
                )
                continue
            eid = f"E{n}"
            n += 1
            items.append(
                EvidenceItem(
                    id=eid,
                    source=f"RFP · {title}",
                    excerpt=text,
                    sectionIds=[sid] if sid else [],
                    chunkKey=f"{_RFP_CHUNK_PREFIX}{sid}:{index}",
                )
            )
            index += 1
    return items


def merge_citation_maps(
    *maps: list[CitationGrounding],
) -> list[CitationGrounding]:
    """Union evidence ids that landed on the same claim text.

    A plan sentence can carry one knowledge-base id and one RFP id. Inline
    provenance wins over overlap when both passes marked the same span.
    """
    order: list[str] = []
    by_key: dict[str, CitationGrounding] = {}
    for cmap in maps:
        for row in cmap:
            key = _normalize(row.text)[:160]
            if not key:
                continue
            prev = by_key.get(key)
            if prev is None:
                ids = list(dict.fromkeys(row.evidence_ids))[:_MAX_MERGED_IDS]
                by_key[key] = row.model_copy(update={"evidence_ids": ids})
                order.append(key)
                continue
            ids = list(prev.evidence_ids)
            for eid in row.evidence_ids:
                if eid not in ids:
                    ids.append(eid)
            method = prev.method
            if "inline_provenance" in (prev.method, row.method):
                method = "inline_provenance"
            elif "verbatim" in (prev.method, row.method):
                method = "verbatim"
            by_key[key] = prev.model_copy(
                update={"evidence_ids": ids[:_MAX_MERGED_IDS], "method": method}
            )
    return [by_key[key] for key in order]


def attach_citation_maps_to_draft(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    *,
    label: str = "citation-map",
    rfp_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Stamp citationMap (+ kbRefs union) on every section. Does not rewrite prose.

    When research.budget is a pricing_plan, Cost sections get mechanical citations
    to Pricing Book catalog/build sources (and those evidence rows are merged into
    research.evidence_corpus in place for badge tooltips).

    RFP requirements for each section are merged the same way. Knowledge-base
    grounding still uses the corpus without those rows, so a section that has
    no tagged packet keeps searching the shared KB instead of only its RFP lines.
    """
    from app.services.proposal_evidence_corpus import ensure_unique_evidence_ids

    corpus = ensure_unique_evidence_ids(
        list((research.evidence_corpus if research else None) or [])
    )
    if research is not None and corpus != list(research.evidence_corpus or []):
        research.evidence_corpus = corpus
        logger.info("%s evidence ids made unique count=%s", label, len(corpus))
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

    rfp_evidence = build_rfp_requirement_evidence(
        draft.sections,
        research,
        start_index=_next_evidence_num(corpus),
        rfp_text=rfp_text,
    )
    if rfp_evidence:
        corpus = merge_prefixed_evidence_into_corpus(
            corpus, rfp_evidence, _RFP_CHUNK_PREFIX
        )
        rfp_evidence = [
            e for e in corpus if (e.chunk_key or "").startswith(_RFP_CHUNK_PREFIX)
        ]
        if research is not None:
            research.evidence_corpus = corpus
        logs.append(
            f"{label}: added {len(rfp_evidence)} RFP requirement source citation(s)"
        )
        logger.info(
            "%s rfp_requirement_sources=%s",
            label,
            len(rfp_evidence),
        )

    if not corpus:
        return draft, logs

    kb_corpus = [
        e
        for e in corpus
        if not (e.chunk_key or "").startswith(_RFP_CHUNK_PREFIX)
    ]

    new_sections: list[ProposalSection] = []
    total = 0
    for section in draft.sections:
        content = drop_unsupported_inline_markers(
            section.content or "",
            kb_corpus,
            section_id=section.id or "",
        )
        sid = section.id or ""
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
            # Pricing rows are tagged to the cost section. Do not let an untagged
            # fall-through pull another section's pricing excerpts onto this body.
            pricing_for_section = [
                e
                for e in pricing_evidence
                if sid and sid in (e.section_ids or [])
            ]
            cmap = ground_budget_pricing_citations(
                content, budget, evidence=pricing_evidence
            )
            if pricing_for_section:
                cmap = merge_citation_maps(
                    cmap,
                    ground_section_citations(
                        content, pricing_for_section, section_id=sid
                    ),
                )
        else:
            cmap = ground_section_citations(content, kb_corpus, section_id=sid)

        section_rfp = [
            e for e in rfp_evidence if sid and sid in (e.section_ids or [])
        ]
        if section_rfp:
            cmap = merge_citation_maps(
                cmap,
                ground_section_citations(content, section_rfp, section_id=sid),
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
            section.model_copy(
                update={"content": content, "citation_map": cmap, "kb_refs": refs}
            )
        )
    logs.append(
        f"{label}: grounded {total} claim citation(s) across {len(new_sections)} section(s)"
    )
    logger.info("%s", logs[-1])
    return draft.model_copy(update={"sections": new_sections}), logs


_STAFF_SECTION_TOKENS = ("team", "personnel", "staff", "bio", "qualif")


def section_may_name_staff(section_id: str, title: str) -> bool:
    """Staffing tabs are the ones allowed to name people from the KB roster."""
    blob = f"{title} {section_id}".casefold()
    return any(token in blob for token in _STAFF_SECTION_TOKENS)


async def seed_writer_citation_packet(
    corpus: list[EvidenceItem],
    *,
    rfp_id: str,
    rfp_sections: list,
    rfp_text: str,
    requirement_ledger: Any = None,
) -> list[EvidenceItem]:
    """Add citable rows before the writer runs.

    Verbatim solicitation spans go on every section that owns them.
    Bio excerpts go on staffing sections only, and only for people the
    knowledge base currently has a resume for. Names are not taken from a
    hardcoded roster.
    """
    from app.models.proposal import RfpSectionMap

    sections = [
        ProposalSection(
            id=getattr(section, "id", "") or "",
            title=getattr(section, "title", "") or "",
            content="seed",
            status="outline",
        )
        for section in rfp_sections
        if getattr(section, "id", None)
    ]
    research = ProposalResearchCache(
        rfpId=rfp_id or "rfp",
        updatedAt="seed",
        rfpSections=[
            section if isinstance(section, RfpSectionMap) else RfpSectionMap.model_validate(section)
            for section in rfp_sections
        ],
        requirementLedger=requirement_ledger,
        evidenceCorpus=list(corpus),
    )
    rfp_rows = build_rfp_requirement_evidence(
        sections,
        research,
        start_index=_next_evidence_num(corpus),
        rfp_text=rfp_text,
    )
    merged = merge_prefixed_evidence_into_corpus(list(corpus), rfp_rows, _RFP_CHUNK_PREFIX)
    logger.info(
        "writer citation seed rfp_rows=%s rfp_id=%s",
        len(rfp_rows),
        rfp_id,
    )

    staff_ids = [
        section.id
        for section in sections
        if section_may_name_staff(section.id, section.title)
    ]
    if not staff_ids:
        from app.services.proposal_evidence_corpus import ensure_unique_evidence_ids

        return ensure_unique_evidence_ids(merged)

    from app.services.proposal_sections_graph import _fetch_member_bio_kb
    from app.services.team_personas_service import get_all_key_personas

    import asyncio

    personas = [
        person
        for person in await get_all_key_personas()
        if not person.get("retired")
        and person.get("hasResume")
        and str(person.get("name") or "").strip()
    ]

    async def _one(person: dict[str, Any]) -> EvidenceItem | None:
        name = str(person.get("name") or "").strip()
        try:
            excerpt, sources = await _fetch_member_bio_kb(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("writer bio seed failed name=%s err=%s", name, exc)
            return None
        text = (excerpt or "").strip()
        if len(text) < 80:
            logger.info("writer bio seed skipped name=%s reason=thin_excerpt", name)
            return None
        source = (sources[0] if sources else "") or str(person.get("sourceFile") or "04_Bio")
        return EvidenceItem(
            id="E0",
            source=source[:240],
            excerpt=text[:2000],
            sectionIds=list(staff_ids),
            chunkKey=f"bio:{person.get('id') or name.casefold()}",
        )

    loaded = await asyncio.gather(*(_one(person) for person in personas))
    bio_rows = [row for row in loaded if row is not None]
    logger.info(
        "writer citation seed bio_rows=%s staff_sections=%s rfp_id=%s",
        len(bio_rows),
        len(staff_ids),
        rfp_id,
    )
    from app.services.proposal_evidence_corpus import ensure_unique_evidence_ids

    return ensure_unique_evidence_ids(
        merge_prefixed_evidence_into_corpus(merged, bio_rows, "bio:")
    )
