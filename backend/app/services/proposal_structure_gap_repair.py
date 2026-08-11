"""Structural manuscript hygiene: empty subheads, truncated sentences."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_quality import word_count

logger = logging.getLogger(__name__)

_HANGING_LAST_WORDS = frozenset(
    {
        "to",
        "with",
        "and",
        "or",
        "the",
        "a",
        "an",
        "of",
        "for",
        "by",
        "from",
        "into",
        "onto",
        "our",
        "their",
        "its",
    }
)


def _is_heading_line(line: str) -> bool:
    s = (line or "").lstrip()
    return s.startswith("#") and not s.startswith("#!")


def _heading_text(line: str) -> str:
    s = (line or "").lstrip()
    while s.startswith("#"):
        s = s[1:]
    return s.strip()


def find_empty_subheadings(content: str) -> list[str]:
    """Return ##/### titles that have no body before the next heading."""
    lines = (content or "").splitlines()
    empty: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_heading_line(lines[i]):
            i += 1
            continue
        title = _heading_text(lines[i])
        raw = lines[i].lstrip()
        level = 0
        for ch in raw:
            if ch == "#":
                level += 1
            else:
                break
        i += 1
        body_bits: list[str] = []
        while i < len(lines) and not _is_heading_line(lines[i]):
            body_bits.append(lines[i])
            i += 1
        body = "\n".join(body_bits).strip()
        if level >= 2 and (not body or word_count(body) < 5):
            if title:
                empty.append(title)
    return empty


def find_truncated_passages(content: str) -> list[str]:
    """Return short excerpts that look cut off mid-thought (structural)."""
    found: list[str] = []
    for para in (content or "").split("\n\n"):
        text = para.strip()
        if not text or _is_heading_line(text):
            continue
        if text.startswith("[") and text.endswith("]"):
            continue
        words = text.replace("\n", " ").split()
        if len(words) < 6:
            continue
        last = words[-1].rstrip(".,;:)!?\"'").casefold()
        ends_ok = text[-1] in ".!?\"')"
        if not ends_ok or last in _HANGING_LAST_WORDS:
            found.append(text[:120])
    return found


def section_structure_issues(section: ProposalSection) -> list[str]:
    content = section.content or ""
    issues: list[str] = []
    for h in find_empty_subheadings(content):
        issues.append(f"empty subheading: {h}")
    for t in find_truncated_passages(content)[:3]:
        issues.append(f"truncated passage: {t}")
    return issues


async def repair_structure_gaps_in_draft(
    draft: ProposalDraft,
    *,
    rfp_id: str,
    rfp: object | None = None,
    rfp_client: str = "",
    rfp_title: str = "",
    max_sections: int = 8,
) -> tuple[ProposalDraft, list[str]]:
    """Fill empty subheads / finish truncated sentences via section repair LLM."""
    from app.services.proposal_self_edit_loop import _repair_one_section

    if rfp is not None:
        rfp_client = rfp_client or getattr(rfp, "client", "") or ""
        rfp_title = rfp_title or getattr(rfp, "title", "") or ""

    logs: list[str] = []
    sections = list(draft.sections)
    repaired = 0
    for section in sections:
        if repaired >= max_sections:
            break
        if section.id.startswith("section-2-bio"):
            continue
        issues = section_structure_issues(section)
        if not issues:
            continue
        empty = [i for i in issues if i.startswith("empty subheading:")]
        trunc = [i for i in issues if i.startswith("truncated passage:")]
        parts: list[str] = []
        if empty:
            parts.append(
                "Fill these empty subheadings with concise factual prose from KB evidence only: "
                + "; ".join(e.split(": ", 1)[1] for e in empty[:6])
                + ". Do not leave headers without body copy."
            )
        if trunc:
            parts.append(
                "Finish or remove truncated mid-sentence passages — every sentence must complete. "
                "Do not invent website builds or mortgage calculators unless KB work type supports them."
            )
        parts.append(
            "Keep designer notes. Do not invent clients, metrics, phones, or emails. "
            "Use ClientList-accurate work types only."
        )
        message = " ".join(parts)
        if rfp is None:
            logs.append(
                f"Structure repair skipped for “{section.title}”: no RFP record"
            )
            continue
        try:
            _sid, improved, detail = await _repair_one_section(
                rfp_id,
                section.id,
                use_senior_editor=False,
                rfp=rfp,  # type: ignore[arg-type]
                rfp_client=rfp_client,
                rfp_title=rfp_title,
                budget=None,
                repair_message=message,
            )
            if improved:
                repaired += 1
                logs.append(
                    f"Structure repair “{section.title}”: {detail or 'updated'} "
                    f"({len(issues)} issue(s))"
                )
                from app.services.proposal_repository import aget_proposal_draft

                latest = await aget_proposal_draft(rfp_id)
                if latest:
                    draft = latest
                    sections = list(draft.sections)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Structure repair failed for %s: %s", section.id, str(exc)[:160]
            )
            logs.append(f"Structure repair skipped for “{section.title}”: {exc}")

    if repaired and draft.sections:
        draft = draft.model_copy(
            update={"updated_at": datetime.now(timezone.utc).isoformat()}
        )
    return draft, logs
