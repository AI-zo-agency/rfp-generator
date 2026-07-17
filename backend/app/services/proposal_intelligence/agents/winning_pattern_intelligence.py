"""Winning Pattern Intelligence — writing patterns from similar wins, not prose."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.retrieval import retrieve_intelligence
from app.services.proposal_intelligence.schemas import (
    ProposalExecutionPlan,
    SectionPlan,
    SectionPlans,
    WinningPattern,
)

logger = logging.getLogger(__name__)
AGENT = "winning_pattern_intelligence"

_FORBIDDEN_PATTERN_KEYS = {
    "content",
    "excerpt",
    "evidence",
    "text",
    "prose",
    "paragraph",
    "paragraphs",
    "sample",
    "sampleText",
    "quote",
    "quotes",
}

_SYSTEM = """Winning Pattern Intelligence Agent.
You analyze similar WON proposal excerpts to extract reusable writing patterns.

Hard rules:
- Do NOT return proposal prose, excerpts, quotes, rewritten sentences, or paragraphs.
- Do NOT paraphrase prior proposal content.
- Return only structured patterns about section opening, flow, persuasion, tone, visuals, differentiators, objections, proof themes, and avoidances.
- sourceWonProposals may contain ids/titles only.

Return JSON only:
{
  "patterns": [
    {
      "sectionId": "section id from outline",
      "sourceWonProposals": ["filename or id"],
      "openingPattern": "string",
      "structureFlow": ["Challenge", "Approach", "Phases", "QA", "Outcomes"],
      "persuasionTechniques": ["string"],
      "commonDifferentiators": ["string"],
      "commonObjections": ["string"],
      "recommendedWordCount": 900,
      "recommendedVisuals": ["string"],
      "avoid": ["string"],
      "commonProofThemes": ["string"],
      "confidence": 0.0
    }
  ],
  "confidence": 0.0
}
"""


class _PatternAssignment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    winning_pattern: WinningPattern = Field(alias="winningPattern")


def _strip_prose_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_prose_keys(item)
            for key, item in value.items()
            if key not in _FORBIDDEN_PATTERN_KEYS
        }
    if isinstance(value, list):
        return [_strip_prose_keys(item) for item in value]
    return value


def _assignment_from_raw(raw_item: dict[str, Any]) -> _PatternAssignment | None:
    cleaned = _strip_prose_keys(raw_item)
    section_id = str(cleaned.get("sectionId") or cleaned.get("section_id") or "").strip()
    if not section_id:
        return None
    pattern_raw = cleaned.get("winningPattern") or cleaned
    try:
        pattern = WinningPattern.model_validate(pattern_raw)
    except Exception as exc:
        logger.warning("%s pattern validation failed for %s: %s", AGENT, section_id, exc)
        pattern = WinningPattern(confidence=0.2)
    pattern.confidence = clamp_confidence(pattern.confidence)
    return _PatternAssignment(sectionId=section_id, winningPattern=pattern)


def _default_pattern(section_title: str, sources: list[str]) -> WinningPattern:
    return WinningPattern(
        sourceWonProposals=sources[:5],
        openingPattern="Start with the evaluator's problem before introducing zö's approach.",
        structureFlow=["Client challenge", "Recommended approach", "Delivery shape", "Proof themes"],
        persuasionTechniques=["Risk reduction", "Outcome-driven messaging"],
        recommendedWordCount=800,
        avoid=["Do not copy prior proposal prose", "Avoid generic marketing language"],
        commonProofThemes=[section_title],
        confidence=0.35 if sources else 0.2,
    )


def _query_for_plan(plan: ProposalExecutionPlan) -> str:
    u = plan.opportunity.understanding
    section_titles = [section.title for section in plan.writing.proposal_outline.sections]
    return (
        f"{u.client} {u.industry} {u.org_type} {u.project_type} "
        f"{' '.join(u.services)} {' '.join(section_titles[:8])} won proposal writing patterns"
    )


async def _extract_patterns(
    *,
    plan: ProposalExecutionPlan,
    excerpts: list[dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    u = plan.opportunity.understanding
    outline_sections = plan.writing.proposal_outline.sections
    return await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Opportunity:\n{u.model_dump_json()}\n\n"
                    f"Outline sections:\n"
                    f"{json.dumps([s.model_dump(by_alias=True) for s in outline_sections], indent=2)}\n\n"
                    "Won proposal excerpts for pattern extraction only. "
                    "Do not return or paraphrase their text:\n"
                    f"{json.dumps(excerpts, indent=2)}"
                ),
            },
        ],
        max_tokens=4096,
        agent_name=AGENT,
    )


def _assignments_from_raw(
    raw: dict[str, Any] | None,
    source_names: list[str],
) -> dict[str, WinningPattern]:
    assignments: dict[str, WinningPattern] = {}
    raw_patterns = raw.get("patterns") if isinstance(raw, dict) else None
    for item in raw_patterns or []:
        if not isinstance(item, dict):
            continue
        assignment = _assignment_from_raw(item)
        if not assignment:
            continue
        if not assignment.winning_pattern.source_won_proposals:
            assignment.winning_pattern.source_won_proposals = source_names[:5]
        assignments[assignment.section_id] = assignment.winning_pattern
    return assignments


def _merge_patterns_into_section_plans(
    plan: ProposalExecutionPlan,
    assignments: dict[str, WinningPattern],
    source_names: list[str],
) -> list[SectionPlan]:
    existing_by_id = {p.section_id: p for p in plan.writing.section_plans.plans}
    plans: list[SectionPlan] = []
    for section in plan.writing.proposal_outline.sections:
        existing = existing_by_id.get(section.id)
        pattern = assignments.get(section.id) or (
            existing.winning_pattern if existing else _default_pattern(section.title, source_names)
        )
        if not pattern.source_won_proposals and source_names:
            pattern.source_won_proposals = source_names[:5]
        if existing:
            plans.append(existing.model_copy(update={"winning_pattern": pattern}))
            continue
        plans.append(
            SectionPlan(
                sectionId=section.id,
                title=section.title,
                purpose=f"Address {section.title}",
                winningPattern=pattern,
            )
        )
    return plans


def _pattern_confidence(raw: dict[str, Any] | None, plans: list[SectionPlan]) -> float:
    confidence = clamp_confidence(
        float((raw or {}).get("confidence") or 0.0) if isinstance(raw, dict) else 0.0
    )
    if confidence <= 0:
        return min([p.winning_pattern.confidence for p in plans] or [0.2])
    return confidence


async def run_winning_pattern_intelligence(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    _ = rfp_meta
    outline_sections = plan.writing.proposal_outline.sections
    if not outline_sections:
        return plan

    hits = await retrieve_intelligence("won_patterns", query=_query_for_plan(plan), limit=5)
    excerpts = [
        {"source": h.get("source"), "excerpt": str(h.get("excerpt") or "")[:1400]}
        for h in hits
    ]
    source_names = [
        str(h.get("source") or "").strip() for h in hits if str(h.get("source") or "").strip()
    ]

    raw, provider = await _extract_patterns(plan=plan, excerpts=excerpts)
    assignments = _assignments_from_raw(raw if isinstance(raw, dict) else None, source_names)
    plans = _merge_patterns_into_section_plans(plan, assignments, source_names)
    confidence = _pattern_confidence(raw if isinstance(raw, dict) else None, plans)
    plan.writing.section_plans = SectionPlans(plans=plans, confidence=confidence)
    plan.metadata.won_patterns_used = list(
        dict.fromkeys(plan.metadata.won_patterns_used + source_names)
    )[:12]
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Winning writing patterns: {len(assignments)} section(s)",
        reason=f"Pattern-only extraction from {len(hits)} won-proposal hit(s)",
        confidence=confidence,
    )
    return plan

