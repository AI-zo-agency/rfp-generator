"""Editorial Validation Agent — review-only for all Sections 1–3."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services import llm
from app.services.llm import LlmError
from app.services.company_qualification.schemas import (
    CompanyTruth,
    EditorialReviewResult,
    ProposalContext,
    Section1ContentBudget,
    Section1PlanResult,
    TeamSelectionResult,
    EvidenceSelectionResult,
)

logger = logging.getLogger(__name__)


def _format_sections_for_review(sections: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for section in sections:
        sid = section.get("id") or ""
        if not str(sid).startswith(("section-1-", "section-2-", "section-3-")):
            continue
        title = section.get("title") or sid
        content = str(section.get("content") or "").strip()
        if not content:
            continue
        word_count = len(content.split())
        lines.append(f"### {title} ({sid})\nWord count: {word_count}\n{content}")
    return "\n\n".join(lines)


async def run_editorial_validation_agent(
    *,
    sections: list[dict[str, Any]],
    company_truth: CompanyTruth,
    proposal_context: ProposalContext,
    section1_plan: Section1PlanResult | None = None,
    team_selection: TeamSelectionResult | None = None,
    evidence_selection: EvidenceSelectionResult | None = None,
) -> tuple[EditorialReviewResult, str]:
    sections_text = _format_sections_for_review(sections)
    budget: Section1ContentBudget | None = (
        section1_plan.content_budget if section1_plan else None
    )
    budgets_json = budget.model_dump_json() if budget else "{}"

    team_json = team_selection.model_dump_json() if team_selection else "{}"
    evidence_json = evidence_selection.model_dump_json() if evidence_selection else "{}"

    messages = [
        {
            "role": "system",
            "content": (
                "You are the Editorial Validation Agent (Rachel review) for zö agency "
                "Sections 1–3.\n"
                "REVIEW ONLY — do NOT rewrite or return replacement section content as final.\n"
                "Flag issues across ALL subsections:\n"
                "- Irrelevant content, duplicates, wrong person/case study/cert\n"
                "- Length violations, weak evidence, too generic, missing proof\n"
                "- Omit-tier capabilities appearing, narrative in business info\n"
                "- Wrong team members vs RFP, too many bios or case studies\n\n"
                "Return at MOST the 8 highest-impact recommendations. Keep each 'issue' and "
                "'recommendation' under 200 characters. Set 'suggestedReplacement' to null. "
                "This keeps the JSON small enough to return complete.\n\n"
                "Return JSON:\n"
                "{\n"
                '  "recommendations": [\n'
                "    {\n"
                '      "sectionId": "string",\n'
                '      "sectionTitle": "string",\n'
                '      "issueType": "redundant_content|length|irrelevant|missing_verify|wrong_person|wrong_evidence|other",\n'
                '      "issue": "what is wrong",\n'
                '      "recommendation": "rewrite|delete|replace|trim — specific guidance",\n'
                '      "confidence": 0.0,\n'
                '      "suggestedReplacement": null,\n'
                '      "status": "pending"\n'
                "    }\n"
                "  ]\n"
                "}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"RFP context:\n{proposal_context.model_dump_json()}\n\n"
                f"Section 1 budgets:\n{budgets_json}\n\n"
                f"Team selection:\n{team_json}\n\n"
                f"Evidence selection:\n{evidence_json}\n\n"
                f"Company truth sources: {company_truth.sources}\n\n"
                f"All composed sections:\n{sections_text[:100000]}"
            ),
        },
    ]

    # Editorial validation is REVIEW-ONLY and must NEVER fail the pipeline.
    # A bad/truncated LLM response yields an empty review instead of an error.
    try:
        raw, provider = await llm.chat_json(
            messages,
            max_tokens=4096,
            temperature=0.1,
        )
    except LlmError as exc:
        logger.warning("Editorial validation LLM call failed (non-fatal): %s", str(exc)[:200])
        return EditorialReviewResult(), "none"
    except Exception as exc:  # noqa: BLE001 — never let review crash generation
        logger.warning("Editorial validation unexpected error (non-fatal): %s", str(exc)[:200])
        return EditorialReviewResult(), "none"

    try:
        review = EditorialReviewResult.model_validate(raw)
    except Exception as exc:
        logger.warning("EditorialReviewResult validation failed: %s", exc)
        review = EditorialReviewResult()

    return review, provider


def editorial_reviewed_at() -> str:
    return datetime.now(timezone.utc).isoformat()
