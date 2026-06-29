"""Shared prompt blocks for proposal drafting."""

from __future__ import annotations

from typing import Any

MODULAR_APPROACH_BLOCK = """## MODULAR TECHNICAL APPROACH (use for approach / marketing plan / work plan sections)

Structure the section in four swappable phases — each phase is a standalone block evaluators can scan:

**Phase 1 — Discover:** stakeholder interviews, document review, audience analysis, success metrics
**Phase 2 — Strategize:** message architecture, channel plan, bilingual/creative brief as required
**Phase 3 — Create:** creative development, production, Spanish variants if required
**Phase 4 — Activate:** launch, optimization cadence, reporting rhythm

Include one **impact-first insight** — a specific, client-centered recommendation that challenges a default assumption
(e.g., validate message comprehension in a pilot market before statewide spend).

Add [DESIGNER NOTE: At a Glance timeline — horizontal milestone graphic] for scannability."""


def is_modular_approach_section(title: str) -> bool:
    t = title.lower()
    return any(
        sig in t
        for sig in (
            "approach",
            "marketing plan",
            "work plan",
            "methodology",
            "scope of work",
            "project plan",
            "campaign plan",
        )
    )


def format_proof_points_block(
    proof_points: list[dict[str, Any]],
    *,
    section_id: str = "",
    section_title: str = "",
) -> str:
    if not proof_points:
        return ""

    relevant = proof_points
    if section_id:
        tagged = [
            p
            for p in proof_points
            if section_id in (p.get("sectionIds") or p.get("section_ids") or [])
        ]
        if tagged:
            relevant = tagged

    if not relevant:
        relevant = sorted(
            proof_points,
            key=lambda p: -(p.get("evaluationWeight") or p.get("evaluation_weight") or 0),
        )[:6]

    lines = [
        "## PROOF POINTS (lead with these, first person we/our)",
        "Use these verified case studies as 'why we win' evidence. Do not invent metrics.",
    ]
    for point in relevant[:8]:
        req = point.get("requirement") or ""
        case = point.get("caseStudy") or point.get("case_study") or ""
        hook = point.get("narrativeHook") or point.get("narrative_hook") or ""
        source = point.get("kbSource") or point.get("kb_source") or ""
        lines.append(f"- Requirement: {req}")
        lines.append(f"  Proof: {case} ({source})")
        if hook:
            lines.append(f"  Hook: {hook}")

    if section_title:
        lines.insert(1, f"Section: {section_title}")

    return "\n".join(lines)


def format_weight_priority_block(sections: list[dict[str, Any]]) -> str:
    weighted = [
        s
        for s in sections
        if (s.get("evaluationWeight") or s.get("evaluation_weight"))
    ]
    if not weighted:
        return ""

    ranked = sorted(
        weighted,
        key=lambda s: -(s.get("evaluationWeight") or s.get("evaluation_weight") or 0),
    )
    lines = [
        "## SCORING PRIORITY (draft highest-weight sections with deepest proof and detail)",
    ]
    for s in ranked[:8]:
        w = s.get("evaluationWeight") or s.get("evaluation_weight")
        title = s.get("title") or s.get("id")
        target = s.get("wordTarget") or s.get("word_target") or ""
        extra = f" — target ~{target} words" if target else ""
        lines.append(f"- {w}%: {title}{extra}")
    return "\n".join(lines)
