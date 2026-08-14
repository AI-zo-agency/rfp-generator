"""Structured recap for section chat — what the agent did, changed, and found."""

from __future__ import annotations

from app.models.proposal import ProposalAgentActivity
from app.services.proposal_section_quality import word_count


def build_improve_agent_activity(
    *,
    section_title: str,
    before: str,
    after: str,
    draft_changed: bool,
    assistant_message: str = "",
    extra_changes: list[str] | None = None,
    extra_discrepancies: list[str] | None = None,
) -> ProposalAgentActivity:
    title = (section_title or "this section").strip() or "this section"
    steps = [
        f"Read “{title}” and your instruction",
        "Checked the draft against the fee ledger / KB where this tab needs facts",
        "Looked for discrepancies (placeholders, grounding, protected tags)",
        "Applied edits or left the manuscript unchanged",
    ]
    changes: list[str] = []
    if draft_changed and (before or "") != (after or ""):
        bw = word_count(before)
        aw = word_count(after)
        changes.append(f"Updated “{title}” ({bw} → {aw} words).")
        if "{{budget." in (before or "") and "{{budget." not in (after or ""):
            changes.append("Replaced unresolved budget placeholders with ledger figures.")
        if "[MANUAL FILL" in (before or "") and "[MANUAL FILL" not in (after or ""):
            changes.append("Resolved MANUAL FILL handoff(s) in this tab.")
        elif "[MANUAL FILL" in (after or "") and "[MANUAL FILL" not in (before or ""):
            changes.append("Kept / restored MANUAL FILL handoff(s) for Sonja.")
    else:
        changes.append("No manuscript text was changed.")
    for extra in extra_changes or []:
        if extra.strip() and extra.strip() not in changes:
            changes.append(extra.strip())

    discrepancies = [d.strip() for d in (extra_discrepancies or []) if d.strip()]
    blob = f"{after or ''}\n{assistant_message or ''}"
    if "BUDGET_GROUNDING" in blob or "BUDGET NEEDS REVIEW" in blob.upper():
        note = "Budget grounding still needs review (manuscript vs canonical ledger)."
        if note not in discrepancies:
            discrepancies.append(note)
    if "{{budget." in (after or ""):
        note = "Unresolved {{budget.}} tokens remain in this tab."
        if note not in discrepancies:
            discrepancies.append(note)

    if discrepancies:
        outcome = "needs_review"
    elif draft_changed and (before or "") != (after or ""):
        outcome = "ok"
    else:
        outcome = "unchanged"
    return ProposalAgentActivity(
        outcome=outcome,
        steps=steps,
        changes=changes,
        discrepancies=discrepancies,
    )
