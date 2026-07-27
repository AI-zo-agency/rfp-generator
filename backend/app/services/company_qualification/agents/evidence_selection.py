"""Evidence Selection Agent — score and select case studies before full retrieval."""

from __future__ import annotations

import logging
import re

from app.services import llm
from app.services.company_qualification.schemas import (
    EvidenceCandidate,
    EvidenceScore,
    EvidenceSelectionResult,
    ProposalContext,
)
from app.services.evidence_trust.client_list import ClientListRegistry
from app.services.evidence_trust.gate import ClaimIntent, GateDecision, gate_client_for_claim
from app.services.evidence_trust.load_client_list import load_client_list_registry
from app.services.evidence_trust.provenance import is_win_eligible, provenance_block_reason
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


def _min_case_studies_for_rfp(rfp_context: str, proposal_context: ProposalContext) -> int:
    """RFP portfolio language → at least 2; otherwise still prefer 2 when available."""
    blob = " ".join(
        [
            " ".join(proposal_context.services_requested or []),
            proposal_context.summary or "",
            (rfp_context or "")[:6000],
        ]
    ).casefold()
    if re.search(
        r"\b(minimum\s+(?:of\s+)?two|at\s+least\s+two|sample\s+work\s+portfolio|"
        r"two\s+recent\s+campaigns|two\s+case\s+stud)\b",
        blob,
    ):
        return 2
    return 2


def _infer_services_claim(proposal_context: ProposalContext, rfp_context: str) -> str:
    blob = " ".join(
        [
            " ".join(proposal_context.services_requested or []),
            proposal_context.summary or "",
            (rfp_context or "")[:2000],
        ]
    ).casefold()
    if any(t in blob for t in ("website", "web site", "web redesign", "web development")):
        return "website_build"
    if "mci" in blob or ("meeting" in blob and "conference" in blob):
        return "tourism_mci"
    if any(t in blob for t in ("leisure", "visitor", "destination marketing", "tourism")):
        if "exclud" in blob and "mci" in blob:
            return "tourism_leisure"
        return "destination_marketing"
    return "experience"


def prefilter_evidence_candidates(
    candidates: list[EvidenceCandidate],
    *,
    registry: ClientListRegistry,
    claim: str,
) -> tuple[list[EvidenceCandidate], list[str]]:
    """Drop Confirm / wrong work-type / non-win provenance before LLM selection."""
    intent = ClaimIntent(slot="case_study", claim=claim, require_win_provenance=True)
    kept: list[EvidenceCandidate] = []
    notes: list[str] = []
    for c in candidates:
        hit = {
            "source": c.source or c.title,
            "title": c.title,
            "excerpt": c.snippet,
            "content": c.snippet,
            "metadata": {"fileName": c.source or c.title},
        }
        if not is_win_eligible(hit):
            reason = provenance_block_reason(hit) or "not win-eligible"
            notes.append(f"dropped {c.title}: {reason}")
            continue
        client = None
        blob = f"{c.title}\n{c.snippet}".casefold()
        for entry in sorted(registry.entries, key=lambda e: len(e.name), reverse=True):
            if entry.name.casefold() in blob:
                client = entry.name
                break
        if client:
            gated = gate_client_for_claim(client, registry=registry, intent=intent)
            if gated.decision != GateDecision.ALLOW:
                reason = gated.rejected[0][1] if gated.rejected else gated.decision.value
                notes.append(f"dropped {c.title} ({client}): {reason}")
                continue
        kept.append(c)
    return kept, notes


async def run_evidence_selection_agent(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
    rfp_client: str,
    candidates: list[EvidenceCandidate],
) -> tuple[EvidenceSelectionResult, str]:
    if not candidates:
        return EvidenceSelectionResult(candidatesConsidered=0, selectedStudies=[]), ""

    claim = _infer_services_claim(proposal_context, rfp_context)
    filtered = candidates
    try:
        registry = await load_client_list_registry()
        if registry.entries:
            filtered, filter_notes = prefilter_evidence_candidates(
                candidates, registry=registry, claim=claim
            )
            for note in filter_notes[:12]:
                logger.info("Evidence prefilter: %s", note)
    except Exception as exc:
        logger.warning("Evidence ClientList prefilter skipped: %s", exc)
        filtered = candidates

    if not filtered:
        logger.warning(
            "Evidence selection: all %d candidates gated out for claim=%s",
            len(candidates),
            claim,
        )
        return (
            EvidenceSelectionResult(candidatesConsidered=len(candidates), selectedStudies=[]),
            "evidence_trust_gate",
        )

    catalog_lines = []
    for i, c in enumerate(filtered, 1):
        catalog_lines.append(
            f"{i}. TITLE: {c.title}\n   SNIPPET: {c.snippet[:400]}\n   SOURCE: {c.source}"
        )
    catalog = "\n\n".join(catalog_lines)
    min_studies = _min_case_studies_for_rfp(rfp_context, proposal_context)

    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Evidence Selection Agent for zö agency Section 3.\n"
                        "SELECT the strongest past case studies for THIS RFP.\n"
                        "You are scoring metadata/snippets only — full documents are fetched later.\n"
                        "Compact JSON only — no markdown fences. Finish every brace.\n"
                        "Rationale ≤8 words each.\n\n"
                        "Scoring weights: Industry 35%, Service 30%, Evaluation alignment 20%, "
                        "Proof strength 10%, Recency 5%.\n\n"
                        "STRICT RULES:\n"
                        f"- Do NOT select work for '{rfp_client}' — that is the CURRENT client.\n"
                        "- ONLY titles from the candidate catalog below.\n"
                        f"- Return AT LEAST {min_studies} and at most 5 studies. "
                        "If the RFP asks for a sample portfolio / minimum two campaigns, "
                        "returning only one study is a HARD FAILURE.\n"
                        "- Prefer distinct clients and campaign types (do not pick two near-duplicates).\n"
                        "- Omit weak or irrelevant examples only AFTER the minimum is met.\n"
                        f"- Required claim applicability: '{claim}' — do not pick nearest-topic "
                        "work that does not actually deliver that work type.\n"
                        "- Never treat finalist/loss files as wins.\n\n"
                        "Return JSON:\n"
                        '{"selectedStudies":["Exact Title 1","Exact Title 2"],'
                        '"scores":[{"title":"...","score":0.85,"rationale":"..."}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"proposalType: {proposal_context.proposal_type}\n"
                        f"industry: {proposal_context.industry}\n"
                        f"servicesRequested: {proposal_context.services_requested}\n"
                        f"summary: {(proposal_context.summary or '')[:280]}\n"
                        f"minimumStudiesRequired: {min_studies}\n\n"
                        f"RFP requirements summary:\n{rfp_context[:8000]}\n\n"
                        f"Candidate catalog ({len(filtered)} items, pre-gated):\n{catalog[:40000]}"
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
                selectedStudies=_heuristic_select(
                    filtered, limit=max(min_studies, 3)
                ),
                scores=[],
            ),
            "heuristic",
        )

    selected = raw.get("selectedStudies") or raw.get("selected_studies") or []
    selected = [str(s).strip() for s in selected if str(s).strip()]
    allowed = {c.title.casefold(): c.title for c in filtered}
    normalized: list[str] = []
    for title in selected:
        canonical = allowed.get(title.casefold())
        if not canonical:
            continue
        if canonical not in normalized:
            normalized.append(canonical)
    normalized = normalized[:5]
    if len(normalized) < min_studies:
        # Backfill from catalog so portfolio RFPs never ship with a single case.
        for title in _heuristic_select(filtered, limit=5):
            if title not in normalized:
                normalized.append(title)
            if len(normalized) >= min_studies:
                break
    if not normalized:
        normalized = _heuristic_select(filtered, limit=max(min_studies, 3))

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
