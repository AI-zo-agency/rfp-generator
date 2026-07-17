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

logger = logging.getLogger(__name__)


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

    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Evidence Selection Agent for zö agency Section 3.\n"
                    "SELECT the strongest 3–5 past case studies for THIS RFP.\n"
                    "You are scoring metadata/snippets only — full documents are fetched later.\n\n"
                    "Scoring weights: Industry 35%, Service 30%, Evaluation alignment 20%, "
                    "Proof strength 10%, Recency 5%.\n\n"
                    "STRICT RULES:\n"
                    f"- Do NOT select work for '{rfp_client}' — that is the CURRENT client.\n"
                    "- ONLY titles from the candidate catalog below.\n"
                    "- Return 3–5 studies maximum. Never return more than 5.\n"
                    "- Omit weak or irrelevant examples.\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "selectedStudies": ["Exact Title 1"],\n'
                    '  "scores": [{"title": "...", "score": 0.85, "rationale": "..."}]\n'
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Proposal context:\n{proposal_context.model_dump_json()}\n\n"
                    f"RFP requirements summary:\n{rfp_context[:15000]}\n\n"
                    f"Candidate catalog ({len(candidates)} items — titles/snippets only):\n{catalog[:80000]}"
                ),
            },
        ],
        temperature=0.0,
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
