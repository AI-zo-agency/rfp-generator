"""Evidence Selection Agent — score and select case studies before full retrieval."""

from __future__ import annotations

import logging

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
from app.services.proposal_case_study_fit import CaseStudyFitReport
from app.services.proposal_case_study_eligibility import (
    is_eligible_section3_case_study_title,
)

logger = logging.getLogger(__name__)


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
    rfp_id: str | None = None,
) -> tuple[list[str], CaseStudyFitReport | None, str]:
    """Same resolver as Match studies — strong fits, then closest 03_CS, RFP min."""
    from app.services.proposal_case_study_match import resolve_case_study_selection
    from app.services.proposal_rfp_compulsory_content import required_case_study_minimum

    min_studies = await required_case_study_minimum(rfp_context)
    try:
        resolved = await resolve_case_study_selection(
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            rfp_title=rfp_title,
            rfp_id=rfp_id,
            rfp_context=rfp_context,
            services_requested=list(proposal_context.services_requested or []),
            min_count=min_studies,
            max_count=max(5, min_studies),
            fetch_full_text=True,
        )
    except Exception as exc:  # noqa: BLE001 - fit is advisory; never block Section 3
        logger.warning("Case study resolver skipped: %s", str(exc)[:180])
        return [], None, "none"

    if resolved.titles:
        logger.info(
            "Case study resolver picked %d (%s): %s",
            len(resolved.titles),
            resolved.match_quality,
            [resolved.display_names.get(t, t) for t in resolved.titles],
        )
    else:
        logger.warning(
            "Case study resolver found no 03_CS matches for capabilities=%s",
            resolved.capabilities[:4],
        )
    return resolved.titles, resolved.fit_report, resolved.match_quality


async def run_evidence_selection_agent(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
    rfp_client: str,
    candidates: list[EvidenceCandidate],
    rfp_sector: str = "",
    rfp_title: str = "",
    rfp_id: str | None = None,
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

    fit_titles, _fit_report, match_quality = await _fit_rank_case_studies(
        proposal_context=proposal_context,
        rfp_context=rfp_context,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_title=rfp_title,
        rfp_id=rfp_id,
    )
    fit_selected = _prefer_fit_ranked(
        fit_titles, filtered, limit=max(5, len(fit_titles) or 2)
    )
    if fit_selected:
        rationale = (
            "RFP capability strong fit"
            if match_quality == "strong"
            else "Closest KB match — review before use"
        )
        scores = [
            EvidenceScore(
                title=t,
                score=1.0 if match_quality == "strong" else 0.5,
                rationale=rationale,
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

    # Resolver found nothing in 03_CS — do not LLM-pick unrelated catalog filler.
    logger.warning(
        "Evidence selection: case study resolver returned 0 titles after gating "
        "(%d candidates considered)",
        len(candidates),
    )
    return (
        EvidenceSelectionResult(
            candidatesConsidered=len(candidates),
            selectedStudies=[],
            scores=[],
        ),
        "case_study_fit_empty",
    )
