"""Trim large research payloads for HTTP responses (full data stays in DB)."""

from __future__ import annotations

from typing import Any

from app.models.proposal import EvidenceItem, ProposalDraft, ProposalDraftSnapshot, ProposalResearchCache
from app.services.proposal_manuscript_locks import strip_leaked_markdown_wrappers

_EXCERPT_API_MAX = 500
_MAX_FULFILL_LOG_LINES = 40


def slim_draft_for_api(draft: ProposalDraft) -> dict[str, Any]:
    """Omit snapshot section bodies from list/get responses — full copies stay in DB."""
    data = draft.model_dump(by_alias=True)
    sections = data.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            raw = section.get("content")
            if isinstance(raw, str) and raw.strip():
                section["content"] = strip_leaked_markdown_wrappers(raw)
    slim_snaps: list[dict[str, Any]] = []
    for snap in draft.snapshots or []:
        slim_snaps.append(
            {
                "savedAt": snap.saved_at,
                "label": snap.label,
                "sectionCount": len(snap.sections or []),
                "sections": [],
                "scanSummary": snap.scan_summary,
            }
        )
    data["snapshots"] = slim_snaps
    report = data.get("lastFulfillReport")
    if isinstance(report, dict) and isinstance(report.get("logs"), list):
        logs = report["logs"]
        if len(logs) > _MAX_FULFILL_LOG_LINES:
            data["lastFulfillReport"] = {
                **report,
                "logs": logs[-_MAX_FULFILL_LOG_LINES:],
            }
    return data


def merge_snapshots_for_save(
    incoming: ProposalDraft,
    existing: ProposalDraft | None,
) -> ProposalDraft:
    """Client autosave sends slim snapshots — restore full section copies from DB."""
    if not existing or not existing.snapshots:
        return incoming
    by_saved_at = {s.saved_at: s for s in existing.snapshots}
    merged: list[ProposalDraftSnapshot] = []
    incoming_snaps = incoming.snapshots or []
    if not incoming_snaps:
        return incoming.model_copy(update={"snapshots": list(existing.snapshots)})
    for snap in incoming_snaps:
        if (not snap.sections) and snap.saved_at in by_saved_at:
            merged.append(by_saved_at[snap.saved_at])
        else:
            merged.append(snap)
    return incoming.model_copy(update={"snapshots": merged})


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
