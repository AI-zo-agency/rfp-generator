"""Match KB case studies to an RFP — standalone (button / CLI), not full Go/No-Go."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.rfp import RfpRecord
from app.services.go_no_go_service import _assess_rfp_content, combine_rfp_text
from app.services.proposal_case_study_fit import (
    CaseStudyFitReport,
    assess_case_study_fit,
    capabilities_for_case_study_fit,
    case_study_display_name,
    closest_match_per_capability,
    select_best_case_study_titles,
    select_closest_case_study_titles,
)
from app.services.proposal_knowledge_base_tools import (
    fetch_case_study_candidates_jit,
    fetch_single_case_study,
)
from app.services.proposal_repository import aget_research_cache, asave_research_cache

logger = logging.getLogger(__name__)


class CaseStudyMatchGap(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    capability: str
    gap_reason: str = Field(alias="gapReason")
    closest_title: str = Field(default="", alias="closestTitle")
    closest_display_name: str = Field(default="", alias="closestDisplayName")
    closest_score: float = Field(default=0.0, alias="closestScore")
    closest_excerpt: str = Field(default="", alias="closestExcerpt")


class CaseStudyMatchStudy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    display_name: str = Field(default="", alias="displayName")
    fit_score: float = Field(default=0.0, alias="fitScore")
    fit_label: str = Field(default="", alias="fitLabel")
    capability: str = ""
    excerpt: str = ""
    matched_terms: list[str] = Field(default_factory=list, alias="matchedTerms")


class CaseStudyMatchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    rfp_id: str = Field(alias="rfpId")
    capabilities: list[str] = Field(default_factory=list)
    selected_titles: list[str] = Field(default_factory=list, alias="selectedTitles")
    fit_report: CaseStudyFitReport = Field(alias="fitReport")
    studies: list[CaseStudyMatchStudy] = Field(default_factory=list)
    gaps: list[CaseStudyMatchGap] = Field(default_factory=list)
    match_quality: str = Field(default="none", alias="matchQuality")
    prefetched_at: str | None = Field(default=None, alias="prefetchedAt")
    message: str = ""


def _capabilities_from_rfp(
    rfp: RfpRecord,
    content: Any,
    *,
    analysis: dict[str, Any] | None = None,
) -> list[str]:
    """Capabilities the RFP requires — core matrix rows first, then RFP text themes."""
    rfp_text = combine_rfp_text(content.description, content.pdf_text)
    services: list[str] = []
    matrix = analysis or rfp.go_no_go_analysis or {}
    rows = matrix.get("capabilityMatrix") or matrix.get("capability_matrix") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            req = str(row.get("requirement") or "").strip()
            if not req:
                continue
            is_core = bool(row.get("isCore") or row.get("is_core"))
            if is_core:
                services.append(req)
        if not services:
            for row in rows:
                if isinstance(row, dict) and row.get("requirement"):
                    services.append(str(row["requirement"]).strip())

    return capabilities_for_case_study_fit(
        services_requested=services,
        rfp_context=rfp_text[:30_000],
        rfp_sector=rfp.sector or "",
    )


def _study_rows_from_report(
    report: CaseStudyFitReport,
    selected_titles: list[str],
    study_texts: dict[str, str],
    *,
    include_closest: bool = False,
    rfp_title: str = "",
    rfp_sector: str = "",
) -> list[CaseStudyMatchStudy]:
    rows: list[CaseStudyMatchStudy] = []
    seen: set[str] = set()

    def _add(capability: str, cand: Any) -> None:
        source = (getattr(cand, "source", None) or "").strip()
        if not source:
            return
        key = source.casefold()
        if key in seen:
            return
        seen.add(key)
        excerpt = study_texts.get(source, "") or getattr(cand, "excerpt", "") or ""
        rows.append(
            CaseStudyMatchStudy(
                title=source,
                displayName=case_study_display_name(source),
                fitScore=float(getattr(cand, "fit_score", 0.0) or 0.0),
                fitLabel=getattr(cand, "fit_label", "") or "",
                capability=capability,
                excerpt=excerpt[:2000],
                matchedTerms=list(getattr(cand, "matched_terms", None) or []),
            )
        )

    if include_closest:
        for capability, cand in closest_match_per_capability(
            report, rfp_title=rfp_title, rfp_sector=rfp_sector
        ):
            _add(capability, cand)
    else:
        title_cf = {t.casefold(): t for t in selected_titles}
        for result in report.results or []:
            for cand in result.candidates or []:
                source = (cand.source or "").strip()
                if not source:
                    continue
                key = source.casefold()
                if key not in title_cf and not any(
                    key in t.casefold() or t.casefold() in key for t in selected_titles
                ):
                    continue
                _add(result.capability, cand)

    for title in selected_titles:
        if title.casefold() in seen:
            continue
        rows.append(
            CaseStudyMatchStudy(
                title=title,
                displayName=case_study_display_name(title),
                excerpt=(study_texts.get(title, "") or "")[:2000],
            )
        )
    rows.sort(key=lambda s: s.fit_score, reverse=True)
    return rows


def _gaps_with_closest(
    report: CaseStudyFitReport,
    *,
    rfp_title: str = "",
    rfp_sector: str = "",
) -> list[CaseStudyMatchGap]:
    closest_map = {
        cap: cand
        for cap, cand in closest_match_per_capability(
            report, rfp_title=rfp_title, rfp_sector=rfp_sector
        )
    }
    gaps: list[CaseStudyMatchGap] = []
    for result in report.results or []:
        if not result.gap:
            continue
        closest = closest_map.get(result.capability)
        raw_title = (closest.source if closest else "") or ""
        gaps.append(
            CaseStudyMatchGap(
                capability=result.capability,
                gapReason=result.gap_reason or "",
                closestTitle=raw_title,
                closestDisplayName=case_study_display_name(raw_title) if raw_title else "",
                closestScore=float(closest.fit_score if closest else 0.0),
                closestExcerpt=(closest.excerpt[:600] if closest else "") or "",
            )
        )
    return gaps


class CaseStudySelectionResult(BaseModel):
    """Canonical case study pick — shared by Match button + Section 3 agent loop."""

    model_config = ConfigDict(populate_by_name=True)

    titles: list[str] = Field(default_factory=list)
    display_names: dict[str, str] = Field(default_factory=dict, alias="displayNames")
    fit_report: CaseStudyFitReport = Field(default_factory=CaseStudyFitReport, alias="fitReport")
    match_quality: str = Field(default="none", alias="matchQuality")
    study_texts: dict[str, str] = Field(default_factory=dict, alias="studyTexts")
    capabilities: list[str] = Field(default_factory=list)


async def resolve_case_study_selection(
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_title: str,
    rfp_id: str | None = None,
    rfp_context: str = "",
    services_requested: list[str] | None = None,
    min_count: int = 2,
    max_count: int = 5,
    fetch_full_text: bool = True,
) -> CaseStudySelectionResult:
    """Same matcher as Match studies: strong fits first, then closest 03_CS, min 2."""
    capabilities = capabilities_for_case_study_fit(
        services_requested=list(services_requested or []),
        rfp_context=(rfp_context or "")[:30_000],
        rfp_sector=rfp_sector or "",
    )
    if not capabilities:
        return CaseStudySelectionResult()

    fit_report = await assess_case_study_fit(
        capabilities,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_title=rfp_title,
        rfp_id=rfp_id,
    )

    strong = select_best_case_study_titles(
        fit_report,
        min_count=min_count,
        max_count=max_count,
        rfp_title=rfp_title,
        rfp_sector=rfp_sector,
    )
    selected = list(strong)
    match_quality = "strong" if selected else "none"

    if len(selected) < min_count:
        closest = select_closest_case_study_titles(
            fit_report,
            max_count=max_count,
            rfp_title=rfp_title,
            rfp_sector=rfp_sector,
        )
        seen = {t.casefold() for t in selected}
        for title in closest:
            key = title.casefold()
            if key in seen:
                continue
            selected.append(title)
            seen.add(key)
            if len(selected) >= min_count:
                break
        if not strong and selected:
            match_quality = "closest"
        elif strong and len(selected) > len(strong):
            match_quality = "strong"

    if not selected:
        closest = select_closest_case_study_titles(
            fit_report,
            max_count=max_count,
            rfp_title=rfp_title,
            rfp_sector=rfp_sector,
        )
        selected = closest[:max_count]
        match_quality = "closest" if selected else "none"

    study_texts: dict[str, str] = {}
    fetch_titles = list(selected)
    if fetch_full_text and fetch_titles:
        for title in fetch_titles[: max_count + 3]:
            try:
                text, _ = await fetch_single_case_study(title)
                if text and not text.startswith("(No matching"):
                    study_texts[title] = text[:40_000]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Case study fetch failed for %s: %s", title, exc)

    display_names = {t: case_study_display_name(t) for t in selected}
    return CaseStudySelectionResult(
        titles=selected,
        displayNames=display_names,
        fitReport=fit_report,
        matchQuality=match_quality,
        studyTexts=study_texts,
        capabilities=capabilities,
    )


async def match_case_studies_for_rfp(
    rfp: RfpRecord,
    *,
    save_to_cache: bool = True,
    fetch_full_text: bool = True,
    min_count: int = 2,
    max_count: int = 5,
) -> CaseStudyMatchResult:
    """Rank KB case studies against this RFP's required capabilities."""
    content = _assess_rfp_content(rfp)
    capabilities = _capabilities_from_rfp(rfp, content)
    if not capabilities:
        return CaseStudyMatchResult(
            rfpId=rfp.id,
            message="Could not derive capabilities from RFP — add scope text or run Go/No-Go.",
            fitReport=CaseStudyFitReport(),
        )

    rfp_text = combine_rfp_text(content.description, content.pdf_text)
    services: list[str] = []
    matrix = rfp.go_no_go_analysis or {}
    rows = matrix.get("capabilityMatrix") or matrix.get("capability_matrix") or []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("requirement"):
                if row.get("isCore") or row.get("is_core"):
                    services.append(str(row["requirement"]).strip())

    resolved = await resolve_case_study_selection(
        rfp_client=rfp.client or rfp.title,
        rfp_sector=rfp.sector or "",
        rfp_title=rfp.title,
        rfp_id=rfp.id,
        rfp_context=rfp_text,
        services_requested=services or None,
        min_count=min_count,
        max_count=max_count,
        fetch_full_text=fetch_full_text,
    )
    selected = resolved.titles
    fit_report = resolved.fit_report
    match_quality = resolved.match_quality
    study_texts = resolved.study_texts

    case_corpus, case_sources = await fetch_case_study_candidates_jit(
        rfp_client=rfp.client or rfp.title,
        rfp_sector=rfp.sector or "",
        rfp_context=rfp_text[:30_000],
    )

    gaps = _gaps_with_closest(
        fit_report,
        rfp_title=rfp.title,
        rfp_sector=rfp.sector or "",
    )

    studies = _study_rows_from_report(
        fit_report,
        selected,
        study_texts,
        include_closest=match_quality == "closest",
        rfp_title=rfp.title,
        rfp_sector=rfp.sector or "",
    )
    now = datetime.now(timezone.utc).isoformat()
    capabilities = resolved.capabilities

    if save_to_cache and (selected or case_corpus.strip()):
        from app.models.proposal import ProposalResearchCache

        research = await aget_research_cache(rfp.id)
        payload = {
            "titles": selected,
            "corpus": case_corpus[:80_000],
            "sources": case_sources,
            "studies": study_texts,
            "fit_report_capabilities": capabilities[:10],
            "prefetched_at": now,
            "match_source": "manual_match",
            "match_quality": match_quality,
        }
        if research:
            research = research.model_copy(
                update={"prefetched_case_studies": payload, "updated_at": now}
            )
        else:
            research = ProposalResearchCache(
                rfpId=rfp.id,
                updatedAt=now,
                prefetchedCaseStudies=payload,
            )
        await asave_research_cache(research)

    gap_note = f" {len(gaps)} capability gap(s)." if gaps else ""
    if match_quality == "strong":
        message = (
            f"Matched {len(selected)} strong case study(s) for "
            f"{len(capabilities)} capability(ies).{gap_note}"
        )
    elif match_quality == "closest":
        message = (
            f"No strong fits — showing {len(selected)} closest KB match(es) for "
            f"{len(capabilities)} capability(ies). Review before using in Section 3.{gap_note}"
        )
    else:
        message = (
            f"No case studies found in KB for {len(capabilities)} capability(ies).{gap_note}"
        )

    return CaseStudyMatchResult(
        rfpId=rfp.id,
        capabilities=capabilities,
        selectedTitles=selected,
        fitReport=fit_report,
        studies=studies,
        gaps=gaps,
        matchQuality=match_quality,
        prefetchedAt=now if save_to_cache else None,
        message=message,
    )


async def prefetch_case_studies_after_go_no_go(
    rfp: RfpRecord,
    content: Any,
    analysis: Any,
) -> None:
    """Go/No-Go hook — same matcher as the proposals-page button."""
    del content, analysis
    result = await match_case_studies_for_rfp(rfp, save_to_cache=True, fetch_full_text=True)
    if result.selected_titles:
        logger.info(
            "Go/No-Go prefetched %d case studies for %s: %s",
            len(result.selected_titles),
            rfp.id,
            result.selected_titles,
        )
