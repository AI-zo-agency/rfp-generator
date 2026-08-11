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
from app.services.proposal_case_study_fit import (
    CaseStudyFitReport,
    assess_case_study_fit,
    capabilities_for_case_study_fit,
    select_best_case_study_titles,
)
from app.services.proposal_case_study_eligibility import (
    is_eligible_section3_case_study_title,
)

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


def _title_in_catalog(title: str, catalog: list[EvidenceCandidate]) -> str | None:
    """Map a fit-ranked source filename/title onto an exact catalog title."""
    needle = (title or "").strip().casefold()
    if not needle:
        return None
    for c in catalog:
        t = (c.title or "").strip()
        if not t:
            continue
        low = t.casefold()
        if low == needle or needle in low or low in needle:
            return t
        src = (c.source or "").strip().casefold()
        if src and (src == needle or needle in src or src in needle):
            return t
    return None


def _prefer_fit_ranked(
    fit_titles: list[str],
    catalog: list[EvidenceCandidate],
    *,
    limit: int,
) -> list[str]:
    """Keep only RFP-strong fits that also exist in the gated catalog."""
    out: list[str] = []
    seen: set[str] = set()
    for title in fit_titles:
        canonical = _title_in_catalog(title, catalog)
        if not canonical:
            # Fit search may surface a study not yet in the snippet catalog — keep it.
            canonical = title.strip()
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(canonical)
        if len(out) >= limit:
            break
    return out


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
        title = (c.title or "").strip()
        source = (c.source or "").strip()
        if not is_eligible_section3_case_study_title(title) or (
            source and not is_eligible_section3_case_study_title(source)
        ):
            notes.append(f"dropped {title or source}: not a single-project case study")
            continue
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


async def _fit_rank_case_studies(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
    rfp_client: str,
    rfp_sector: str = "",
    rfp_title: str = "",
) -> tuple[list[str], CaseStudyFitReport | None]:
    """Capability-fit ranker — prefer RFP-best studies over 'any strong catalog title'."""
    capabilities = capabilities_for_case_study_fit(
        services_requested=list(proposal_context.services_requested or []),
        rfp_context=rfp_context,
        rfp_sector=rfp_sector,
    )
    if not capabilities:
        return [], None
    try:
        report = await assess_case_study_fit(
            capabilities,
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            rfp_title=rfp_title,
        )
    except Exception as exc:  # noqa: BLE001 - fit is advisory; never block Section 3
        logger.warning("Case study fit ranking skipped: %s", str(exc)[:180])
        return [], None
    titles = select_best_case_study_titles(
        report,
        min_count=1,
        max_count=3,
        rfp_title=rfp_title,
        rfp_sector=rfp_sector,
    )
    if titles:
        logger.info(
            "Case study fit ranked %d strong studies for capabilities=%s",
            len(titles),
            capabilities[:4],
        )
    else:
        logger.warning(
            "Case study fit found no strong matches for capabilities=%s",
            capabilities[:4],
        )
    return titles, report


async def run_evidence_selection_agent(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
    rfp_client: str,
    candidates: list[EvidenceCandidate],
    rfp_sector: str = "",
    rfp_title: str = "",
) -> tuple[EvidenceSelectionResult, str]:
    if not candidates:
        return EvidenceSelectionResult(candidatesConsidered=0, selectedStudies=[]), ""

    claim = _infer_services_claim(proposal_context, rfp_context)
    # Always drop mega-dumps / org templates before trust gate + fit ranking.
    eligible: list[EvidenceCandidate] = []
    for c in candidates:
        title = (c.title or "").strip()
        source = (c.source or "").strip()
        if is_eligible_section3_case_study_title(
            title, rfp_title=rfp_title, rfp_sector=rfp_sector
        ) and (
            not source
            or is_eligible_section3_case_study_title(
                source, rfp_title=rfp_title, rfp_sector=rfp_sector
            )
        ):
            eligible.append(c)
        else:
            logger.info(
                "Evidence selection dropped ineligible title=%r source=%r",
                title,
                source,
            )
    candidates = eligible or candidates

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

    min_studies = _min_case_studies_for_rfp(rfp_context, proposal_context)

    # Prefer capability-fit ranking so Section 3 proves what the RFP asks for —
    # not the first/strongest titles sitting in the retrieval catalog.
    fit_titles, _fit_report = await _fit_rank_case_studies(
        proposal_context=proposal_context,
        rfp_context=rfp_context,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_title=rfp_title,
    )
    fit_selected = _prefer_fit_ranked(fit_titles, filtered, limit=5)
    # Strong RFP fits win even when fewer than the preferred portfolio count —
    # never pad with off-capability catalog titles just to hit a number.
    if fit_selected:
        scores = [
            EvidenceScore(
                title=t,
                score=1.0,
                rationale="RFP capability strong fit",
            )
            for t in fit_selected
        ]
        return (
            EvidenceSelectionResult(
                candidatesConsidered=len(candidates),
                selectedStudies=fit_selected,
                scores=scores,
            ),
            "case_study_fit",
        )

    # No strong capability fits — LLM picks from the gated catalog with
    # capability-first instructions (still no weak-filler backfill).
    selection_pool = filtered
    catalog_lines = []
    for i, c in enumerate(selection_pool, 1):
        catalog_lines.append(
            f"{i}. TITLE: {c.title}\n   SNIPPET: {c.snippet[:400]}\n   SOURCE: {c.source}"
        )
    catalog = "\n\n".join(catalog_lines)
    capability_hint = ", ".join(
        capabilities_for_case_study_fit(
            services_requested=list(proposal_context.services_requested or []),
            rfp_context=rfp_context,
            rfp_sector=rfp_sector,
        )[:5]
    )

    try:
        raw, provider = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Evidence Selection Agent for zö agency Section 3.\n"
                        "SELECT only case studies that BEST prove the RFP's required capabilities.\n"
                        "You are scoring metadata/snippets only — full documents are fetched later.\n"
                        "Compact JSON only — no markdown fences. Finish every brace.\n"
                        "Rationale ≤8 words each.\n\n"
                        "Scoring weights: Capability match 50%, Service 25%, Industry 15%, "
                        "Proof strength 10%.\n\n"
                        "STRICT RULES:\n"
                        f"- Do NOT select work for '{rfp_client}' — that is the CURRENT client.\n"
                        "- ONLY titles from the candidate catalog below.\n"
                        f"- Prefer studies that demonstrate: {capability_hint or claim}.\n"
                        "- NEVER pick a brand/website/tourism case study just because it is "
                        "strong writing if the RFP asks for a different capability "
                        "(e.g. digital ads / geofencing / paid media).\n"
                        f"- Return UP TO 5 studies. Prefer AT LEAST {min_studies} ONLY if they "
                        "are genuine capability matches. An honest shorter list beats "
                        "padding with irrelevant work.\n"
                        "- Prefer distinct clients and campaign types (do not pick two near-duplicates).\n"
                        "- Omit weak or off-capability examples — do NOT pad to hit a count.\n"
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
                        f"minimumStudiesPreferred: {min_studies}\n"
                        f"rfpCapabilitiesToProve: {capability_hint}\n\n"
                        f"RFP requirements summary:\n{rfp_context[:8000]}\n\n"
                        f"Candidate catalog ({len(selection_pool)} items, pre-gated):\n{catalog[:40000]}"
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
                selectedStudies=_heuristic_select(filtered, limit=max(min_studies, 3)),
                scores=[],
            ),
            "heuristic",
        )

    selected = raw.get("selectedStudies") or raw.get("selected_studies") or []
    selected = [str(s).strip() for s in selected if str(s).strip()]
    allowed = {c.title.casefold(): c.title for c in selection_pool}
    for c in filtered:
        allowed.setdefault((c.title or "").casefold(), c.title)
    normalized: list[str] = []
    for title in selected:
        canonical = allowed.get(title.casefold())
        if not canonical:
            mapped = _title_in_catalog(title, filtered)
            if not mapped:
                continue
            canonical = mapped
        if canonical not in normalized:
            normalized.append(canonical)
    normalized = normalized[:5]
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
