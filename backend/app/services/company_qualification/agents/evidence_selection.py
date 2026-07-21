"""Evidence Selection Agent — score and select case studies before full retrieval."""

from __future__ import annotations

import logging

from app.services import llm
from app.services.company_qualification.schemas import (
    EvidenceCandidate,
    EvidenceScore,
    EvidenceSelectionResult,
    ProposalContext,
)
from app.services.llm import LlmError

logger = logging.getLogger(__name__)


def _heuristic_select(candidates: list[EvidenceCandidate], limit: int = 4) -> list[str]:
    """Zero-cost fallback: keep top catalog titles (already retrieval-ranked)."""
    titles: list[str] = []
    for c in candidates:
        t = (c.title or "").strip()
        if t and t not in titles:
            titles.append(t)
        if len(titles) >= limit:
            break
    return titles


async def run_evidence_selection_agent(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
    rfp_client: str,
    candidates: list[EvidenceCandidate],
) -> tuple[EvidenceSelectionResult, str]:
    if not candidates:
        return EvidenceSelectionResult(candidatesConsidered=0, selectedStudies=[]), ""

    catalog_lines = []
    for i, c in enumerate(candidates, 1):
        catalog_lines.append(
            f"{i}. TITLE: {c.title}\n   SNIPPET: {c.snippet[:400]}\n   SOURCE: {c.source}"
        )
    catalog = "\n\n".join(catalog_lines)

    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Evidence Selection Agent for zö agency Section 3.\n"
                        "SELECT the strongest 3–5 past case studies for THIS RFP.\n"
                        "You are scoring metadata/snippets only — full documents are fetched later.\n"
                        "Compact JSON only — no markdown fences. Finish every brace.\n"
                        "Rationale ≤8 words each.\n\n"
                        "Scoring weights: Industry 35%, Service 30%, Evaluation alignment 20%, "
                        "Proof strength 10%, Recency 5%.\n\n"
                        "STRICT RULES:\n"
                        f"- Do NOT select work for '{rfp_client}' — that is the CURRENT client.\n"
                        "- ONLY titles from the candidate catalog below.\n"
                        "- Return 3–5 studies maximum. Never return more than 5.\n"
                        "- Omit weak or irrelevant examples.\n\n"
                        "Return JSON:\n"
                        '{"selectedStudies":["Exact Title 1"],'
                        '"scores":[{"title":"...","score":0.85,"rationale":"..."}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"proposalType: {proposal_context.proposal_type}\n"
                        f"industry: {proposal_context.industry}\n"
                        f"servicesRequested: {proposal_context.services_requested}\n"
                        f"summary: {(proposal_context.summary or '')[:280]}\n\n"
                        f"RFP requirements summary:\n{rfp_context[:8000]}\n\n"
                        f"Candidate catalog ({len(candidates)} items):\n{catalog[:40000]}"
                    ),
                },
            ],
            max_tokens=1536,
            temperature=0.0,
            tier="light",
        )
    except LlmError as exc:
        logger.warning(
            "Evidence selection LLM failed (%s); using catalog heuristic (no retry)",
            str(exc)[:180],
        )
        return (
            EvidenceSelectionResult(
                candidatesConsidered=len(candidates),
                selectedStudies=_heuristic_select(candidates),
                scores=[],
            ),
            "heuristic",
        )

    selected = raw.get("selectedStudies") or raw.get("selected_studies") or []
    selected = [str(s).strip() for s in selected if str(s).strip()]
    allowed = {c.title.casefold(): c.title for c in candidates}
    normalized: list[str] = []
    for title in selected:
        canonical = allowed.get(title.casefold(), title)
        if canonical not in normalized:
            normalized.append(canonical)
    normalized = normalized[:5]
    if not normalized:
        normalized = _heuristic_select(candidates)

    scores_raw = raw.get("scores") or []
    scores: list[EvidenceScore] = []
    for entry in scores_raw:
        if isinstance(entry, dict) and entry.get("title"):
            try:
                scores.append(EvidenceScore.model_validate(entry))
            except Exception:
                scores.append(
                    EvidenceScore(
                        title=str(entry.get("title")),
                        score=float(entry.get("score") or 0),
                        rationale=str(entry.get("rationale") or ""),
                    )
                )

    result = EvidenceSelectionResult(
        candidatesConsidered=len(candidates),
        selectedStudies=normalized,
        scores=scores,
    )
    return result, provider
