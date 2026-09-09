"""Fill RFP "Table of Contents" tabs from the live manuscript section list.

Page numbers are unknown until designer layout — the Page column uses an em dash
plus a designer note. This is deterministic (no LLM): Review used to skip TOC
rewrites and leave MANUAL FILL stubs empty forever.
"""

from __future__ import annotations

import re

from app.models.proposal import ProposalDraft, ProposalSection

_TOC_TITLE_RE = re.compile(
    r"(?i)\btable\s+of\s+contents\b|^\s*toc\s*$|^\s*contents\s*$"
)
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.)]?\s*")


def is_table_of_contents_section(section: ProposalSection) -> bool:
    title = (section.title or "").strip()
    if not title:
        return False
    bare = _NUMBER_PREFIX_RE.sub("", title).strip()
    return bool(_TOC_TITLE_RE.search(title) or _TOC_TITLE_RE.search(bare))


def _entry_title(section: ProposalSection) -> str:
    return (section.title or section.id or "Untitled").strip()


def _should_list_in_toc(section: ProposalSection) -> bool:
    if is_table_of_contents_section(section):
        return False
    sid = (section.id or "").casefold()
    # Company-block wrapper is a designer header, not a deliverable tab.
    if sid in {
        "company-block-header",
        "section-company-block-header",
        "rfp-structure-company-block-header",
    }:
        return False
    title = _entry_title(section)
    if not title:
        return False
    return True


def build_manuscript_toc_markdown(draft: ProposalDraft) -> str:
    """Markdown TOC table for every deliverable tab currently in the draft."""
    rows: list[str] = []
    for section in draft.sections or []:
        if not _should_list_in_toc(section):
            continue
        title = _entry_title(section).replace("|", "/")
        rows.append(f"| {title} | - |")
    if not rows:
        rows.append("| (sections will appear after drafting) | - |")
    lines = [
        "## Table of Contents",
        "",
        "[DESIGNER NOTE: Assign final page numbers in layout. Hyphens are placeholders.]",
        "",
        "| Section | Page |",
        "| --- | --- |",
        *rows,
        "",
    ]
    return "\n".join(lines)


def fill_table_of_contents_in_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Replace hollow/missing TOC bodies with a live section map."""
    logs: list[str] = []
    toc_md = build_manuscript_toc_markdown(draft)
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections or []:
        if not is_table_of_contents_section(section):
            sections.append(section)
            continue
        body = (section.content or "").strip()
        # Always refresh so Review after reorder stays accurate — cheap + deterministic.
        if body == toc_md.strip():
            sections.append(section)
            continue
        sections.append(
            section.model_copy(
                update={"content": toc_md, "status": "generated"}
            )
        )
        changed = True
        logs.append(
            f"Filled Table of Contents from {sum(1 for s in draft.sections if _should_list_in_toc(s))} live section(s)"
        )
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs
