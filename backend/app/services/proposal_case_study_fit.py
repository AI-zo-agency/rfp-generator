"""Case-study fit: rank zö case studies against what the RFP actually requires
the vendor to DO, instead of defaulting every section to "any case study."

REAL DEFECT THIS FIXES (human QA of a generated proposal): a digital-advertising-
only RFP got three case studies, only one of which (a geofencing/digital-ad
engagement) demonstrated a digital advertising capability. The other two — a
brand-messaging engagement and a website redesign — were cited as proof of
"similar work" while showing no digital advertising component at all.

`proposal_intelligence.jit_retrieval._infer_claim_from_entry` was part of the
cause: it is a hardcoded keyword ladder that falls through to a generic
"experience" claim for anything it does not recognize, which lets ANY case
study satisfy ANY section. Nothing scored a candidate against the specific
capability the RFP was scoring.

This module is the fix for case-study retrieval specifically:
  1. ONE LLM call per proposal (never per section, never per candidate) reads
     the RFP's required capabilities and plans Supermemory queries + match
     keywords for each — modeled on `proposal_section_editor.REFINE_QUERIES_PROMPT`,
     the best query-planning prompt in this codebase (buyer name forbidden,
     doc-type hints required).
  2. Those queries are fetched via the existing Supermemory client.
  3. Fit is scored deterministically — token overlap between the capability
     (+ LLM-supplied match keywords) and each candidate's text — so ranking
     candidates costs zero extra LLM calls no matter how many hits come back.
  4. When nothing clears the strong-fit bar, that is reported as an explicit
     gap. The closest-but-wrong candidate is never relabeled as a match — a
     weak case study presented confidently is worse than an acknowledged gap.

Node name: "case_select" — already reserved (unused until this module) in
`app.services.llm_routing._MECHANICAL_EXACT`. This call selects/ranks; it does
not compose client-facing prose and does not itself judge factual correctness,
so it belongs on the mechanical/light tier, matching the existing
`query_planner` precedent in `proposal_langchain_agents.py`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.services import supermemory
from app.services.llm import LlmError, chat_json
from app.services.proposal_knowledge_base_tools import normalize_zo_kb_query

logger = logging.getLogger(__name__)

NODE_NAME = "case_select"

# Below this token-overlap ratio a candidate does not count as demonstrating
# the capability — it may still be shown (transparency), but only as a weak
# fit, and it can never clear a capability's gap on its own.
STRONG_FIT_THRESHOLD = 0.3

MAX_QUERIES_PER_CAPABILITY = 5
MAX_RAW_HITS_PER_CAPABILITY = 15
MAX_CANDIDATES_RETURNED = 6
SEARCH_LIMIT_PER_QUERY = 6
PLANNER_MAX_TOKENS = 1536

_STOPWORDS = frozenset(
    {
        "the", "and", "of", "for", "with", "including", "a", "an", "to", "in",
        "on", "that", "this", "are", "is", "as", "by", "or", "it", "its",
        "from", "be", "will", "we", "our", "you", "your", "at", "not",
    }
)

CASE_STUDY_QUERY_PROMPT = """Case Study Fit Planner for zö agency proposals.

The Supermemory knowledge base holds ONLY zö agency materials — case studies
(03_CS_ prefix), won proposals (06_WON_), bios, company facts. It NEVER
contains the RFP buyer/prospect. NEVER use the RFP buyer/client name as the
subject of a query — it will return nothing and wastes a search.

You are given a list of capabilities the RFP requires the vendor to
demonstrate through case studies / examples of similar work (e.g. "digital
advertising campaigns including geofencing"). A capability described in
general sector terms ("destination marketing") is NOT the same as a
capability describing a specific deliverable ("geofencing digital ads") — do
not water a specific ask down to the sector.

For EACH capability, return:
- "queries": 3-5 Supermemory search queries that would surface a zö case
  study proving that EXACT capability, not merely a case study from the same
  sector. Prefer the 03_CS_ doc-type hint. Name the concrete deliverable or
  medium from the capability itself (e.g. "geofencing", "paid social",
  "programmatic display", "video production") rather than generic words like
  "marketing" or "experience".
- "matchKeywords": 4-8 concrete terms/synonyms that would actually appear in
  a case study that demonstrates this capability (not just its sector).

Return ONLY JSON, one entry per input capability, in the same order:
{
  "capabilities": [
    {
      "capability": "digital advertising campaigns including geofencing",
      "queries": [
        "zö agency 03_CS geofencing digital advertising campaign case study",
        "zö agency 03_CS programmatic display digital ad case study"
      ],
      "matchKeywords": ["geofencing", "digital ad", "programmatic", "display ads", "paid media"]
    }
  ]
}"""


class CaseStudyCandidate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str
    excerpt: str
    chunk_key: str = Field(default="", alias="chunkKey")
    fit_score: float = Field(default=0.0, alias="fitScore")
    fit_label: str = Field(default="weak_fit", alias="fitLabel")
    matched_terms: list[str] = Field(default_factory=list, alias="matchedTerms")


class CaseStudyFitResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    capability: str
    candidates: list[CaseStudyCandidate] = Field(default_factory=list)
    gap: bool = True
    gap_reason: str = Field(default="", alias="gapReason")
    queries_used: list[str] = Field(default_factory=list, alias="queriesUsed")


class CaseStudyFitReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    results: list[CaseStudyFitResult] = Field(default_factory=list)
    provider: str = "none"
    llm_call_count: int = Field(default=0, alias="llmCallCount")

    def result_for(self, capability: str) -> "CaseStudyFitResult | None":
        for result in self.results:
            if result.capability == capability:
                return result
        return None


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").casefold())
    return {t for t in tokens if len(t) > 2 and t not in _STOPWORDS}


def _hit_label(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("customId")
        or metadata.get("fileName")
        or metadata.get("title")
        or hit.get("title")
        or hit.get("id")
        or "document"
    )


def _hit_text(hit: dict[str, Any], *, max_chars: int = 2000) -> str:
    content = (
        hit.get("content")
        or hit.get("memory")
        or hit.get("chunk")
        or hit.get("text")
        or hit.get("summary")
        or ""
    )
    return str(content).strip()[:max_chars]


def _contains_client_name(text: str, rfp_client: str) -> bool:
    client = (rfp_client or "").strip()
    if len(client) < 3:
        return False
    return client.casefold() in (text or "").casefold()


def _clean_queries(
    raw_queries: Sequence[str],
    capability: str,
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_title: str,
) -> list[str]:
    """Normalize LLM-planned queries and hard-drop any that still name the buyer.

    `normalize_zo_kb_query` is the existing buyer-name scrubber used by
    `proposal_knowledge_base_tools` (REFINE_QUERIES_PROMPT's query cleanup
    path) — reused here rather than re-invented. The explicit
    `_contains_client_name` drop afterward is a second, unconditional
    backstop: the observed bug this task fixes is a query built as
    `f"zö agency {title} {client}"`, and no amount of best-effort rewriting
    should be trusted alone to prevent that from reappearing.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for q in list(raw_queries)[: MAX_QUERIES_PER_CAPABILITY * 2]:
        normalized = normalize_zo_kb_query(
            str(q), rfp_client=rfp_client, rfp_sector=rfp_sector, rfp_title=rfp_title
        )
        if not normalized or _contains_client_name(normalized, rfp_client):
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
        if len(cleaned) >= MAX_QUERIES_PER_CAPABILITY:
            break
    if not cleaned:
        fallback = normalize_zo_kb_query(
            capability, rfp_client=rfp_client, rfp_sector=rfp_sector, rfp_title=rfp_title
        )
        if fallback and not _contains_client_name(fallback, rfp_client):
            cleaned.append(fallback)
    return cleaned


def _score_hit(hit_tokens: set[str], capability_tokens: set[str]) -> tuple[float, list[str]]:
    if not capability_tokens:
        return 0.0, []
    matched = sorted(capability_tokens & hit_tokens)
    score = round(min(len(matched) / len(capability_tokens), 1.0), 4)
    return score, matched


def _rank_hits(hits: list[dict[str, Any]], capability_tokens: set[str]) -> list[CaseStudyCandidate]:
    candidates: list[CaseStudyCandidate] = []
    for hit in hits:
        text = _hit_text(hit)
        if not text:
            continue
        label = _hit_label(hit)
        hit_tokens = _tokenize(f"{label} {text}")
        score, matched = _score_hit(hit_tokens, capability_tokens)
        candidates.append(
            CaseStudyCandidate(
                source=label,
                excerpt=text[:1200],
                chunk_key=str(hit.get("id") or hit.get("customId") or ""),
                fit_score=score,
                fit_label="strong_fit" if score >= STRONG_FIT_THRESHOLD else "weak_fit",
                matched_terms=matched,
            )
        )
    candidates.sort(key=lambda c: c.fit_score, reverse=True)
    return candidates


def _gap_status(candidates: list[CaseStudyCandidate], capability: str) -> tuple[bool, str]:
    if not candidates:
        return True, f"No knowledge base case study found for '{capability}'."
    if candidates[0].fit_score < STRONG_FIT_THRESHOLD:
        return True, (
            f"No case study in the knowledge base demonstrates '{capability}'. "
            "The closest matches are listed for visibility but do not show this "
            "capability — treat this as a gap, not proof of similar work."
        )
    return False, ""


async def _call_planner(
    capabilities: Sequence[str],
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_title: str,
    rfp_id: str | None,
    run_id: str | None,
) -> tuple[dict[str, Any], str]:
    """The one LLM call. Never raises — returns ({}, "none") on any failure."""
    capability_lines = "\n".join(f"- {c}" for c in capabilities)
    messages = [
        {"role": "system", "content": CASE_STUDY_QUERY_PROMPT},
        {
            "role": "user",
            "content": (
                f"RFP sector: {rfp_sector or 'unspecified'}\n"
                f"RFP title: {rfp_title or 'unspecified'}\n"
                "(Buyer name below is given ONLY so you can recognize and "
                f"exclude it from every query: {rfp_client or 'unspecified'})\n\n"
                f"Required capabilities to demonstrate via zö case studies:\n{capability_lines}"
            ),
        },
    ]
    try:
        raw, provider = await chat_json(
            messages,
            max_tokens=PLANNER_MAX_TOKENS,
            temperature=0.25,
            tier="light",
            node_name=NODE_NAME,
            rfp_id=rfp_id,
            run_id=run_id,
        )
    except LlmError as exc:
        logger.warning("case_study_fit planner LLM failed: %s", str(exc)[:200])
        return {}, "none"
    except Exception as exc:  # noqa: BLE001 - planner failure must degrade, never raise
        logger.warning("case_study_fit planner unexpected error: %s", str(exc)[:200])
        return {}, "none"
    if not isinstance(raw, dict):
        return {}, "none"
    return raw, provider


def _parse_planner_output(raw: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    parsed: dict[str, dict[str, list[str]]] = {}
    for item in raw.get("capabilities") or []:
        if not isinstance(item, dict):
            continue
        capability = str(item.get("capability") or "").strip()
        if not capability:
            continue
        queries = [str(q).strip() for q in (item.get("queries") or []) if str(q).strip()]
        keywords = [str(k).strip() for k in (item.get("matchKeywords") or []) if str(k).strip()]
        parsed[capability] = {"queries": queries, "keywords": keywords}
    return parsed


async def _fetch_hits(queries: list[str]) -> list[dict[str, Any]]:
    raw_hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        try:
            hits = await supermemory.search_documents(
                query=query[:220],
                limit=SEARCH_LIMIT_PER_QUERY,
                include_full_docs=True,
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("case_study_fit fetch failed for query=%r: %s", query[:80], exc)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad query must not sink the report
            logger.warning("case_study_fit unexpected fetch error for query=%r: %s", query[:80], exc)
            continue
        for hit in hits:
            if not supermemory.is_knowledge_base_hit(hit):
                continue
            label = _hit_label(hit)
            key = str(hit.get("id") or hit.get("customId") or label)
            if key in seen:
                continue
            seen.add(key)
            raw_hits.append(hit)
            if len(raw_hits) >= MAX_RAW_HITS_PER_CAPABILITY:
                break
        if len(raw_hits) >= MAX_RAW_HITS_PER_CAPABILITY:
            break
    return raw_hits


async def assess_case_study_fit(
    required_capabilities: Sequence[str],
    *,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_title: str = "",
    rfp_id: str | None = None,
    run_id: str | None = None,
) -> CaseStudyFitReport:
    """Rank zö case studies against each required capability — one LLM call total.

    Degrades gracefully on any LLM or Supermemory failure: every capability
    comes back with `gap=True` and an explanatory `gap_reason`, never a
    fabricated ranking.
    """
    capabilities = [c.strip() for c in required_capabilities if isinstance(c, str) and c.strip()]
    if not capabilities:
        return CaseStudyFitReport()

    raw, provider = await _call_planner(
        capabilities,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_title=rfp_title,
        rfp_id=rfp_id,
        run_id=run_id,
    )
    planned = _parse_planner_output(raw) if provider != "none" else {}

    if provider == "none" or not planned:
        return CaseStudyFitReport(
            results=[
                CaseStudyFitResult(
                    capability=c,
                    gap=True,
                    gap_reason="Case study query planning unavailable — no ranking performed.",
                )
                for c in capabilities
            ],
            provider=provider,
            llm_call_count=1,
        )

    supermemory_available = supermemory.is_configured()
    results: list[CaseStudyFitResult] = []
    for capability in capabilities:
        plan = planned.get(capability)
        if not plan:
            results.append(
                CaseStudyFitResult(
                    capability=capability,
                    gap=True,
                    gap_reason="Planner returned no queries for this capability.",
                )
            )
            continue

        cleaned_queries = _clean_queries(
            plan["queries"],
            capability,
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            rfp_title=rfp_title,
        )

        if not supermemory_available:
            results.append(
                CaseStudyFitResult(
                    capability=capability,
                    gap=True,
                    gap_reason="Supermemory is not configured — no case studies fetched.",
                    queries_used=cleaned_queries,
                )
            )
            continue

        capability_tokens = _tokenize(capability) | _tokenize(" ".join(plan["keywords"]))
        hits = await _fetch_hits(cleaned_queries)
        candidates = _rank_hits(hits, capability_tokens)
        gap, gap_reason = _gap_status(candidates, capability)
        results.append(
            CaseStudyFitResult(
                capability=capability,
                candidates=candidates[:MAX_CANDIDATES_RETURNED],
                gap=gap,
                gap_reason=gap_reason,
                queries_used=cleaned_queries,
            )
        )

    return CaseStudyFitReport(results=results, provider=provider, llm_call_count=1)
