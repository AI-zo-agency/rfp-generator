"""Second-pass KPI repairs — fix label-swap artifacts in tables and BMP linkages."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_fulfill_kpi_fix import (
    _CONTRACTOR_KPI_PAREN,
    _CONTRACTOR_KPI_SENTENCE,
)
from app.services.proposal_rfp_excerpt import evaluation_and_kpi_excerpt

logger = logging.getLogger(__name__)

_ARRIVALS_MEASUREMENT = (
    "Total Visitor Arrivals — **Measurement method:** HTA/Oceania MMA airline passenger (PAX) "
    "and official visitor arrival statistics, reconciled quarterly to the Section 2.3 "
    "+3.0% annual growth target. (Not a resident sentiment or tourism favorability survey.)"
)

_EXPENDITURES_MEASUREMENT = (
    "Total Visitor Expenditures — **Measurement method:** HTA visitor expenditure and "
    "trip-spend tracking (including per-person-per-day where reported), reconciled to the "
    "Section 2.3 +4.6% annual growth target."
)

_ISLANDS_MEASUREMENT = (
    "Average Islands Visited Per Person — **Measurement method:** share of visitors visiting "
    "more than one island / multi-island itinerary rate from HTA visitor profile and MMA "
    "reporting, tracked to the Section 2.3 +0.8% annual growth target. "
    "(Not a trip satisfaction survey.)"
)

# Label-swap debris from old Resident Sentiment / Visitor Satisfaction text.
_FABRICATED_ARRIVALS_SURVEY_RE = re.compile(
    r"Total Visitor Arrivals \(contractor KPI\)\s*Survey[^|\n\r]*?"
    r"(?:favorable rating|residents toward tourism|Independent Total Visitor Arrivals)[^|\n\r]*",
    re.I,
)

_FABRICATED_ISLANDS_SURVEY_RE = re.compile(
    r"Average Islands Visited Per Person \(contractor KPI\)\s*Survey[^|\n\r]*?"
    r"(?:trip satisfaction|satisfaction rating|overall trip)[^|\n\r]*",
    re.I,
)

_FABRICATED_EXPENDITURES_SENTIMENT_RE = re.compile(
    r"Total Visitor Expenditures \(contractor KPI\)\s*Survey[^|\n\r]*?"
    r"(?:favorable rating|resident sentiment|satisfaction)[^|\n\r]*",
    re.I,
)

_ANY_CONTRACTOR_KPI_SURVEY_RE = re.compile(
    r"(Total Visitor Arrivals|Total Visitor Expenditures|Average Islands Visited Per Person)"
    r"\s*\(contractor KPI\)\s*Survey",
    re.I,
)

_DUPLICATE_KPI_INTRO_RE = re.compile(
    r"(three contractor KPIs under Section 2\.3:[^.]+\.)"
    r"\s*—?\s*three Key Performance Indicators\s*"
    r"\(\s*Total Visitor Arrivals,\s*Total Visitor Expenditures,\s*"
    r"and Average Islands Visited Per Person[^)]*\)",
    re.I | re.S,
)

_RESIDENT_SENTIMENT_ROW_RE = re.compile(
    r"\|?\s*Resident Sentiment[^|\n\r]*\|[^|\n\r]*\|[^|\n\r]*\|?\s*\n?",
    re.I,
)

_VISITOR_SATISFACTION_ROW_RE = re.compile(
    r"\|?\s*Visitor Satisfaction[^|\n\r]*\|[^|\n\r]*\|[^|\n\r]*\|?\s*\n?",
    re.I,
)

_KPI_DETAIL_SECTION_HINTS = (
    "activity measure",
    "bmp",
    "brand marketing plan",
    "methodology",
    "market sizing",
    "market knowledge",
    "performance measure",
    "kpi",
    "reporting",
    "analytics",
)


def content_has_kpi_detail_artifacts(content: str) -> bool:
    blob = content or ""
    if not blob.strip():
        return False
    if _FABRICATED_ARRIVALS_SURVEY_RE.search(blob):
        return True
    if _FABRICATED_ISLANDS_SURVEY_RE.search(blob):
        return True
    if _FABRICATED_EXPENDITURES_SENTIMENT_RE.search(blob):
        return True
    if _ANY_CONTRACTOR_KPI_SURVEY_RE.search(blob):
        return True
    if _DUPLICATE_KPI_INTRO_RE.search(blob):
        return True
    cf = blob.casefold()
    if "total visitor arrivals (contractor kpi) survey" in cf:
        return True
    if "favorable rating among" in cf and "total visitor arrivals" in cf:
        return True
    if "trip satisfaction" in cf and "average islands visited" in cf:
        return True
    return False


def sections_needing_kpi_detail_repair(draft: ProposalDraft) -> list[str]:
    ids: list[str] = []
    for section in draft.sections:
        if not (section.content or "").strip():
            continue
        title_cf = (section.title or "").casefold()
        if any(h in title_cf for h in _KPI_DETAIL_SECTION_HINTS) or content_has_kpi_detail_artifacts(
            section.content or ""
        ):
            if content_has_kpi_detail_artifacts(section.content or "") or any(
                h in title_cf for h in ("activity measure", "bmp", "methodology", "market")
            ):
                ids.append(section.id)
    return ids


def repair_kpi_detail_artifacts(content: str) -> tuple[str, list[str]]:
    """Fix swapped-label survey nonsense and duplicate KPI intros — not a full rewrite."""
    if not (content or "").strip():
        return content or "", []

    logs: list[str] = []
    out = content

    if _FABRICATED_ARRIVALS_SURVEY_RE.search(out):
        out = _FABRICATED_ARRIVALS_SURVEY_RE.sub(_ARRIVALS_MEASUREMENT, out)
        logs.append("KPI detail: replaced fabricated Arrivals sentiment survey row/text")

    if _FABRICATED_ISLANDS_SURVEY_RE.search(out):
        out = _FABRICATED_ISLANDS_SURVEY_RE.sub(_ISLANDS_MEASUREMENT, out)
        logs.append("KPI detail: replaced fabricated Islands satisfaction survey text")

    if _FABRICATED_EXPENDITURES_SENTIMENT_RE.search(out):
        out = _FABRICATED_EXPENDITURES_SENTIMENT_RE.sub(_EXPENDITURES_MEASUREMENT, out)
        logs.append("KPI detail: replaced fabricated Expenditures sentiment survey text")

    # Generic "(contractor KPI) Survey" labels without valid instrument text.
    if _ANY_CONTRACTOR_KPI_SURVEY_RE.search(out):
        out = re.sub(
            r"Total Visitor Arrivals\s*\(contractor KPI\)\s*Survey",
            "Total Visitor Arrivals — data source",
            out,
            flags=re.I,
        )
        out = re.sub(
            r"Total Visitor Expenditures\s*\(contractor KPI\)\s*Survey",
            "Total Visitor Expenditures — data source",
            out,
            flags=re.I,
        )
        out = re.sub(
            r"Average Islands Visited Per Person\s*\(contractor KPI\)\s*Survey",
            "Average Islands Visited Per Person — data source",
            out,
            flags=re.I,
        )
        logs.append("KPI detail: removed bogus '(contractor KPI) Survey' labels")

    if _DUPLICATE_KPI_INTRO_RE.search(out):
        out = _DUPLICATE_KPI_INTRO_RE.sub(r"\1", out)
        logs.append("KPI detail: deduped repeated KPI intro in same paragraph")

    # Collapse sentence + paren duplicate when both canonical blocks appear adjacently.
    combo = f"{_CONTRACTOR_KPI_SENTENCE} — {_CONTRACTOR_KPI_PAREN}"
    if combo in out:
        out = out.replace(combo, _CONTRACTOR_KPI_SENTENCE)
        logs.append("KPI detail: merged redundant KPI intro blocks")

    if _RESIDENT_SENTIMENT_ROW_RE.search(out):
        out = _RESIDENT_SENTIMENT_ROW_RE.sub("", out)
        logs.append("KPI detail: removed legacy Resident Sentiment table row")

    if _VISITOR_SATISFACTION_ROW_RE.search(out):
        out = _VISITOR_SATISFACTION_ROW_RE.sub("", out)
        logs.append("KPI detail: removed legacy Visitor Satisfaction table row")

    # Soft-fix incoherent BMP linkages (deterministic nudge — LLM pass may refine).
    out = re.sub(
        r"(Ma[ʻ']?ema[ʻ']?e[^.\n]{0,120}?)KPI Linkage:\s*Total Visitor Arrivals\s*\(contractor KPI\)",
        r"\1KPI Linkage: Total Visitor Expenditures (contractor KPI) — brand quality supports visitor spend",
        out,
        flags=re.I,
    )
    if "Ma" in content and "KPI Linkage" in content and out != content:
        logs.append("KPI detail: adjusted Maʻemaʻe creative KPI linkage toward expenditures")

    return out, logs


def run_kpi_detail_deterministic_pass(
    draft: ProposalDraft,
    *,
    skip_section_ids: set[str],
) -> tuple[ProposalDraft, list[str]]:
    from app.models.proposal import ProposalDraft as DraftModel

    logs: list[str] = []
    sections = list(draft.sections)
    changed = False
    for idx, section in enumerate(sections):
        if section.id in skip_section_ids:
            continue
        body = section.content or ""
        if not body.strip():
            continue
        fixed, fix_logs = repair_kpi_detail_artifacts(body)
        if fixed != body:
            sections[idx] = section.model_copy(update={"content": fixed, "status": "generated"})
            logs.extend(fix_logs)
            changed = True
    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def _llm_rewrite_kpi_detail_section(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_excerpt: str,
) -> str:
    stub = section.content or ""
    if not llm.is_configured():
        return stub

    system = (
        "Rewrite ONE proposal section so contractor KPI **detail** matches RFP Section 2.3.\n"
        "Top-level KPI names are already correct — fix **measurement instruments**, "
        "**Activity Measure table rows**, and **BMP tactic-to-KPI linkages**.\n\n"
        "Rules:\n"
        "- Total Visitor Arrivals: count-based — airline PAX / HTA arrival statistics. "
        "NEVER describe as a resident sentiment or 'favorable rating toward tourism' survey.\n"
        "- Total Visitor Expenditures: spend / PPPD / HTA expenditure tracking — not satisfaction.\n"
        "- Average Islands Visited Per Person: multi-island visit rate / itinerary share — "
        "NOT 'trip satisfaction rating'.\n"
        "- Remove contradictory duplicate rows for the same KPI (keep one measurement story).\n"
        "- BMP KPI Linkage lines must be logically causal (e.g. brand/creative → expenditures; "
        "itinerary/island content → islands visited; demand media → arrivals).\n"
        "- Do NOT reintroduce agency four-KPI language (Resident Sentiment, Visitor Satisfaction, "
        "Average Daily Visitor Spending as a set).\n"
        "- Do NOT rewrite team bios, FEIN, insurance, or budget tables.\n"
        "- Keep markdown tables valid.\n"
        "Return JSON: {\"content\": \"full section markdown\"}"
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section: {section.title} ({section.id})\n\n"
        f"RFP KPI / evaluation excerpt:\n{rfp_excerpt[:32000]}\n\n"
        f"Current section (fix measurement + linkages):\n{stub[:14000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=8192,
            temperature=0.25,
        )
        content = str((raw or {}).get("content") or "").strip()
        return content or stub
    except Exception as exc:  # noqa: BLE001
        logger.warning("KPI detail LLM rewrite failed for %s: %s", section.id, exc)
        return stub


async def run_kpi_detail_thorough_pass(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    skip_section_ids: set[str],
    use_llm: bool,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Deterministic detail repair + optional LLM rewrite for KPI-heavy sections."""
    human: list[str] = []
    draft, logs = run_kpi_detail_deterministic_pass(draft, skip_section_ids=skip_section_ids)

    target_ids = [
        sid
        for sid in sections_needing_kpi_detail_repair(draft)
        if sid not in skip_section_ids
    ]
    # Always include sections that still have artifacts after deterministic pass.
    for section in draft.sections:
        if section.id in skip_section_ids:
            continue
        if content_has_kpi_detail_artifacts(section.content or ""):
            if section.id not in target_ids:
                target_ids.append(section.id)

    if not target_ids:
        logs.append("KPI detail: no Activity Measure / BMP artifacts detected.")
        return draft, logs, human

    logs.append(f"KPI detail: {len(target_ids)} section(s) need measurement/linkage repair.")

    if not use_llm:
        remaining = [
            s.title
            for s in draft.sections
            if s.id in target_ids and content_has_kpi_detail_artifacts(s.content or "")
        ]
        if remaining:
            human.append(
                "KPI detail still inconsistent in: "
                + ", ".join(remaining[:6])
                + " — re-run Scan RFP with LLM enabled for full rewrite."
            )
        return draft, logs, human

    excerpt = evaluation_and_kpi_excerpt(rfp_text) or rfp_text[:80000]
    sections = list(draft.sections)
    changed = False
    for idx, section in enumerate(sections):
        if section.id not in target_ids:
            continue
        new_content = await _llm_rewrite_kpi_detail_section(
            section=section,
            rfp=rfp,
            rfp_excerpt=excerpt,
        )
        new_content, fix_logs = repair_kpi_detail_artifacts(new_content)
        logs.extend(fix_logs)
        if new_content.strip() and new_content != (section.content or ""):
            sections[idx] = section.model_copy(
                update={"content": new_content, "status": "generated"}
            )
            logs.append(f"KPI detail LLM: rewrote “{section.title}”")
            changed = True

    if changed:
        now = datetime.now(timezone.utc).isoformat()
        draft = draft.model_copy(update={"sections": sections, "updated_at": now})

    still_bad = [
        s.title
        for s in draft.sections
        if s.id in target_ids and content_has_kpi_detail_artifacts(s.content or "")
    ]
    if still_bad:
        human.append(
            "After KPI detail pass, review measurement wording in: "
            + ", ".join(still_bad[:8])
        )
        logs.append(f"KPI detail: {len(still_bad)} section(s) may still need human review.")

    return draft, logs, human
