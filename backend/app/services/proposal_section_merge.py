"""Deterministic merge of mislabeled duplicate sidebar sections (chat structure ops).

When the user asks to keep one tab's body under another tab's title and delete
the orphan, do that directly — never run proposal-wide duplicate paragraph stripping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_forms_attachments_integrity import section_is_forms_attachments

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionMergePlan:
    content_section_id: str
    title_section_id: str
    drop_section_id: str
    reason: str


def user_asks_structural_section_merge(text: str) -> bool:
    """True when the user wants a sidebar tab merged/deleted — not a duplicate audit."""
    raw = (text or "").casefold()
    if "duplicate" not in raw:
        return False
    wants_delete = "delete" in raw and "section" in raw
    wants_rehome = (
        ("keep" in raw and ("move" in raw or "under" in raw or "title" in raw))
        or "mislabeled" in raw
        or "orphan" in raw
    )
    return wants_delete and wants_rehome


def _body_is_forms_attachments(content: str) -> bool:
    head = (content or "").lstrip()[:120].casefold()
    return head.startswith("# required forms") or "## submission compliance" in head[:800].casefold()


def find_forms_attachments_duplicate_pair(
    draft: ProposalDraft,
) -> tuple[ProposalSection, ProposalSection] | None:
    """Two tabs whose body is Required Forms & Attachments prose."""
    hits: list[ProposalSection] = []
    for section in draft.sections:
        body = section.content or ""
        if section_is_forms_attachments(section) or _body_is_forms_attachments(body):
            hits.append(section)
    if len(hits) < 2:
        return None
    if len(hits) > 2:
        # Prefer canonical + one mislabeled ledger tab.
        canonical = next((s for s in hits if s.id == "rfp-req-forms-attachments"), None)
        mislabeled = next(
            (
                s
                for s in hits
                if s.id != "rfp-req-forms-attachments"
                and "reference" in (s.title or "").casefold()
            ),
            None,
        )
        if canonical and mislabeled:
            return mislabeled, canonical
    return hits[0], hits[1]


def plan_section_merge(
    draft: ProposalDraft,
    user_message: str,
    *,
    open_section_id: str = "",
) -> SectionMergePlan | None:
    """Build a merge plan from the user ask + manuscript shape."""
    if not user_asks_structural_section_merge(user_message):
        return None

    pair = find_forms_attachments_duplicate_pair(draft)
    if pair is None:
        return None

    a, b = pair
    raw = (user_message or "").casefold()

    # Explicit: mislabeled References tab holds forms body → keep its content.
    if "references submission" in raw or (
        "mislabeled" in raw and "reference" in (a.title or "").casefold()
    ):
        content_sec, title_sec = a, b
        if not section_is_forms_attachments(title_sec):
            content_sec, title_sec = b, a
    elif section_is_forms_attachments(b) and not section_is_forms_attachments(a):
        content_sec, title_sec = a, b
    elif section_is_forms_attachments(a) and not section_is_forms_attachments(b):
        content_sec, title_sec = b, a
    else:
        # Default: canonical id keeps title; other keeps content if open there.
        canonical = next(
            (s for s in (a, b) if s.id == "rfp-req-forms-attachments"),
            b,
        )
        other = b if canonical is a else a
        if open_section_id and open_section_id == other.id:
            content_sec, title_sec = other, canonical
        else:
            content_sec, title_sec = other, canonical

    if content_sec.id == title_sec.id:
        return None

    return SectionMergePlan(
        content_section_id=content_sec.id,
        title_section_id=title_sec.id,
        drop_section_id=content_sec.id,
        reason=(
            f"Merge “{content_sec.title}” body into “{title_sec.title}” "
            f"and remove mislabeled duplicate tab."
        ),
    )


def apply_section_merge(
    draft: ProposalDraft,
    plan: SectionMergePlan,
) -> tuple[ProposalDraft, ProposalSection, list[str]]:
    by_id = {s.id: s for s in draft.sections}
    content_sec = by_id.get(plan.content_section_id)
    title_sec = by_id.get(plan.title_section_id)
    drop_sec = by_id.get(plan.drop_section_id)
    if not content_sec or not title_sec or not drop_sec:
        return draft, title_sec or content_sec or draft.sections[0], []

    body = (content_sec.content or "").strip()
    # Ensure H1 matches the kept sidebar title when present.
    if body.startswith("# ") and (title_sec.title or "").strip():
        lines = body.split("\n", 1)
        lines[0] = f"# {title_sec.title.strip()}"
        body = "\n".join(lines)

    merged = title_sec.model_copy(update={"content": body + "\n", "status": "generated"})
    sections = [
        merged if s.id == title_sec.id else s
        for s in draft.sections
        if s.id != drop_sec.id
    ]
    now = datetime.now(timezone.utc).isoformat()
    updated = draft.model_copy(update={"sections": sections, "updated_at": now})
    logs = [
        plan.reason,
        f"Removed duplicate tab “{drop_sec.title}” ({drop_sec.id}).",
        f"Kept “{title_sec.title}” ({title_sec.id}) with merged body.",
    ]
    logger.info("section_merge %s", logs)
    return updated, merged, logs


def format_merge_reply(logs: list[str], *, focus_title: str) -> str:
    lines = [f"**{focus_title} — structure merge applied**", ""]
    for item in logs:
        lines.append(f"- {item}")
    return "\n".join(lines)
