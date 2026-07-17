"""Trim large research payloads for HTTP responses (full data stays in DB)."""

from __future__ import annotations

from app.models.proposal import EvidenceItem, ProposalResearchCache

_EXCERPT_API_MAX = 500


def slim_research_for_api(research: ProposalResearchCache) -> ProposalResearchCache:
    """Shorten evidence excerpts in API responses to avoid proxy timeouts."""
    corpus: list[EvidenceItem] = []
    for item in research.evidence_corpus:
        excerpt = item.excerpt or ""
        if len(excerpt) > _EXCERPT_API_MAX:
            excerpt = excerpt[:_EXCERPT_API_MAX] + "…"
        corpus.append(item.model_copy(update={"excerpt": excerpt}))

    plan = research.proposal_execution_plan
    slim_plan = plan
    if plan is not None and hasattr(plan, "model_dump"):
        dumped = plan.model_dump(by_alias=True)
        slim_plan = {
            "metadata": dumped.get("metadata"),
            "validation": dumped.get("validation"),
            "proposalMemory": dumped.get("proposalMemory"),
            "writing": {
                "proposalOutline": (dumped.get("writing") or {}).get("proposalOutline"),
                "sectionPlans": {
                    "plans": [
                        {
                            "sectionId": p.get("sectionId"),
                            "title": p.get("title"),
                            "purpose": p.get("purpose"),
                            "wordBudget": p.get("wordBudget"),
                        }
                        for p in (
                            ((dumped.get("writing") or {}).get("sectionPlans") or {}).get(
                                "plans"
                            )
                            or []
                        )
                    ],
                    "confidence": ((dumped.get("writing") or {}).get("sectionPlans") or {}).get(
                        "confidence"
                    ),
                },
                "retrievalPlan": (dumped.get("writing") or {}).get("retrievalPlan"),
            },
        }
    elif isinstance(plan, dict):
        slim_plan = {
            "metadata": plan.get("metadata"),
            "validation": plan.get("validation"),
            "proposalMemory": plan.get("proposalMemory"),
        }

    return research.model_copy(
        update={
            "evidence_corpus": corpus,
            "proposal_execution_plan": slim_plan,
        }
    )
