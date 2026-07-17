"""Shared prompt blocks for proposal drafting."""

from __future__ import annotations

from typing import Any

# Anti-hallucination rules - CRITICAL for all proposal generation
ANTI_HALLUCINATION_RULES = """
## CRITICAL: ANTI-HALLUCINATION RULES

YOU MUST NEVER:
1. Invent statistics (retention rates, client counts, audience sizes, years of experience)
2. Cite specific numbers unless they appear VERBATIM in the evidence corpus with [E#] citation
3. Use team member names that are not in approved bio files (04_Bio_*.pdf)
4. Add certifications not explicitly listed in 01_companyfacts_verified
5. Transfer metrics from one client project to describe agency-wide capabilities
6. Round or approximate numbers - use exact figures from KB or use [VERIFY: specific field]
7. Spell names incorrectly (check exact spelling in bio file names)
8. Claim "X years of Y experience" unless that exact phrasing is in verified facts

VERIFIED FACTS ONLY:
- Agency founded: 2012 (13 years total as zö agency)
- Certifications: WBENC, WOSB (ONLY these two are verified)
- Client retention: DO NOT cite a specific average retention rate (not formally tracked)
- Awards: Creative Excellence 2024, Netty 2024, NYX 2024, Vega Digital 2024, Sonja's Enterprising Women 2026
- Team: ONLY use names from approved 04_Bio_*.pdf files in KB
- Insurance: Use [VERIFY: insurance field] for all coverage amounts and details except what's explicit in KB

IF YOU CANNOT VERIFY A FACT:
- Use [VERIFY: specific field needed] instead of inventing
- Never use phrases like "approximately," "around," "over X years" without KB evidence
- Do not embellish or extrapolate from partial information

CERTIFICATIONS & INSURANCE:
- Keep these sections SHORT and CONCISE
- List only verified certifications (WBENC, WOSB)
- For insurance: state coverage types only, use [VERIFY: amounts] for dollar figures
- Do not add platform certifications (Google Ads, Meta, etc.) unless they appear in verified KB
"""

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
