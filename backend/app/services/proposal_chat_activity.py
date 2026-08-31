"""Structured recap for section chat — what the agent did, changed, and found."""

from __future__ import annotations

import re

from app.models.proposal import ProposalAgentActivity
from app.services.proposal_section_quality import word_count

_HEADING_IN_TABLE_CELL_RE = re.compile(r"(?m)\|[^|\n]*#{1,6}\s")
_PARTIAL_SELECTION_REPLY_RE = re.compile(
    r"(?is)revised\s+only\s+your\s+selected\s+excerpt|only\s+your\s+selected\s+excerpt|"
    r"rest\s+of\s+the\s+section\s+is\s+unchanged"
)
# Recap-only — keep local so activity never depends on improve_pin import order.
_THOROUGH_ASK_FOR_RECAP_RE = re.compile(
    r"(?is)"
    r"\b(?:fix|address|resolve|correct|handle|cover|repair)\b.{0,32}\b(?:all|every|each)\b"
    r"|"
    r"\b(?:all|every)\s+(?:issue|problem|defect|gap|item|point|concern)s?\b"
    r"|"
    r"\b(?:add|include|insert|put)\b.{0,40}\b(?:table|subsection|heading|block)\b"
    r"|"
    r"\b(?:as\s+well|too|also)\b.{0,40}\b(?:add|include|table|section|fix|address)\b"
    r"|"
    r"^\s*(?:\d+[\).]|[-*•])\s+\S"
)


def collect_chat_edit_discrepancies(
    *,
    before: str,
    after: str,
    user_message: str = "",
    assistant_message: str = "",
) -> list[str]:
    """Lightweight post-edit checks for Revise content / Improve section recaps."""
    notes: list[str] = []
    blob = f"{after or ''}\n{assistant_message or ''}"
    if "BUDGET_GROUNDING" in blob or "BUDGET NEEDS REVIEW" in blob.upper():
        notes.append("Budget grounding still needs review (manuscript vs canonical ledger).")
    if "{{budget." in (after or ""):
        notes.append("Unresolved {{budget.}} tokens remain in this tab.")
    if "«MFILL_" in (after or "") and "«MFILL_" not in (before or ""):
        notes.append(
            "Invented «MFILL_N» placeholder tokens — not valid MANUAL FILL handoffs. "
            "Use [MANUAL FILL: …] or KB-backed facts."
        )
    if _HEADING_IN_TABLE_CELL_RE.search(after or ""):
        notes.append("Markdown heading leaked into a table cell — fix table structure.")
    ask = (user_message or "").strip()
    reply = (assistant_message or "").strip()
    if ask and _THOROUGH_ASK_FOR_RECAP_RE.search(ask) and _PARTIAL_SELECTION_REPLY_RE.search(
        reply
    ):
        notes.append(
            "Instruction needed a full-section fix; only a highlighted excerpt was revised."
        )
    return notes


def build_improve_agent_activity(
    *,
    section_title: str,
    before: str,
    after: str,
    draft_changed: bool,
    assistant_message: str = "",
    user_message: str = "",
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
    for note in collect_chat_edit_discrepancies(
        before=before or "",
        after=after or "",
        user_message=user_message or "",
        assistant_message=assistant_message or "",
    ):
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
