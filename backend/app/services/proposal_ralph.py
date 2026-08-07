"""Ralph — RFP fidelity controller (concise writing + no invented assets).

Page fit is enforced at draft-time via wordTarget allocation (see
``allocate_word_budget`` / ``_remaining_word_budget``). Ralph does NOT
hard-chop the manuscript after the fact — that destroyed unique endings
while leaving duplicated openers. Ralph's job is anti-invention scrub +
prompt rules for concision / no rehash.
"""

from __future__ import annotations

import logging
import re

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.rfp_page_limit import resolve_page_limit

logger = logging.getLogger(__name__)

WORDS_PER_PAGE = 350
_SHORT_RFP_PAGE_THRESHOLD = 15
_NON_DRAFT_RESERVE_DEFAULT = 0.15
_NON_DRAFT_RESERVE_SHORT = 0.22
# Soft overshoot only: if a single section blows past its own wordTarget,
# trim that section — never rescale the whole document after generation.
_SECTION_OVERSHOOT_RATIO = 1.25

RALPH_RFP_FIDELITY_RULES = """
## RALPH — RFP FIDELITY (mandatory)

You are writing under Ralph's RFP rules. Accuracy and concision beat volume.
ZERO REPETITION: never restate facts already owned by another section.

PAGE / LENGTH DISCIPLINE:
1. Obey wordTarget as a HARD CEILING, not a goal. Shorter is better when the
   RFP asks are answered. Do not pad or expand for length.
2. If the RFP states a page limit, plan dense copy that can fit — but write
   each section to its allocated wordTarget (do not dump extra pages "for
   the designer to cut").
3. Never write content for later whittling. Deliver submission-length copy.

ANTI-REPETITION:
4. Do NOT re-copy Who We Are, FEIN/address/certs, full bios, or full case
   studies. One short cross-reference, then NEW detail only for THIS tab.
5. Do NOT paraphrase another RFP tab (Approach ≠ Methodology rewrite).

ANTI-INVENTION (assets / diagrams / tools):
6. Do NOT invent reporting dashboards, process diagrams, org charts, timeline
   graphics, sample screens, portals, or software we do not have evidenced in KB.
7. Do NOT add [DESIGNER NOTE: … graphic/diagram/illustration …] unless THIS RFP
   explicitly requires that visual OR a verified KB/template asset exists.
8. If the RFP asks for a diagram/report sample we cannot evidence: write one
   short sentence + [VERIFY: provide diagram/report sample — not in KB].
9. Prefer text tables and concise narrative over invented visual gadgets.
"""

_INVENTED_DESIGNER_NOTE_RE = re.compile(
    r"\[DESIGNER\s+NOTE:[^\]]*(?:diagram|graphic|illustration|infographic|"
    r"timeline\s+graphic|milestone\s+graphic|dashboard|org\s+chart|"
    r"process\s+map|flow\s+chart|wireframe|mockup|screenshot)[^\]]*\]",
    re.IGNORECASE,
)

_INVENTED_ASSET_CLAIM_RE = re.compile(
    r"(?im)^[^\n]*(?:attached\s+(?:is|are)|please\s+see|see\s+(?:the\s+)?(?:enclosed|attached))"
    r"[^\n]*(?:diagram|dashboard|infographic|org\s+chart|process\s+map|"
    r"reporting\s+(?:portal|dashboard)|sample\s+report\s+graphic)[^\n]*$",
)


def ralph_non_draft_reserve_fraction(page_limit: int | None) -> float:
    if page_limit and 0 < page_limit <= _SHORT_RFP_PAGE_THRESHOLD:
        return _NON_DRAFT_RESERVE_SHORT
    return _NON_DRAFT_RESERVE_DEFAULT


def ralph_document_word_budget(
    page_limit: int | None,
    *,
    already_spent_words: int = 0,
) -> int | None:
    """Advisory manuscript word budget from RFP page limit (draft-time planning)."""
    if not page_limit or page_limit <= 0:
        return None
    total = int(page_limit * WORDS_PER_PAGE)
    reserve = int(total * ralph_non_draft_reserve_fraction(page_limit))
    return max(0, total - reserve - max(0, already_spent_words))


def strip_invented_asset_promises(content: str) -> tuple[str, list[str]]:
    """Remove invented designer-visual notes and fake 'see attached diagram' lines."""
    if not content or not content.strip():
        return content, []
    logs: list[str] = []
    out = content

    def _drop_note(match: re.Match[str]) -> str:
        logs.append(f"ralph:removed-designer-note:{match.group(0)[:80]}")
        return ""

    out = _INVENTED_DESIGNER_NOTE_RE.sub(_drop_note, out)

    kept_lines: list[str] = []
    for line in out.splitlines():
        if _INVENTED_ASSET_CLAIM_RE.search(line):
            logs.append(f"ralph:removed-invented-asset-claim:{line.strip()[:80]}")
            kept_lines.append(
                "[VERIFY: RFP-requested visual/report sample — provide from real "
                "assets; do not invent]"
            )
            continue
        kept_lines.append(line)
    out = "\n".join(kept_lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, logs


def _word_count(text: str) -> int:
    return len((text or "").split())


def _trim_to_word_ceiling(content: str, ceiling: int) -> tuple[str, int]:
    words = _word_count(content)
    if ceiling <= 0 or words <= ceiling:
        return content, 0
    paragraphs = re.split(r"\n\s*\n", content.strip())
    while paragraphs and _word_count("\n\n".join(paragraphs)) > ceiling:
        paragraphs.pop()
    trimmed = "\n\n".join(paragraphs).strip()
    if not trimmed:
        parts = content.split()
        trimmed = " ".join(parts[:ceiling]).strip()
    removed = max(0, words - _word_count(trimmed))
    return trimmed, removed


def apply_ralph_to_section(
    section: ProposalSection,
    *,
    word_ceiling: int | None = None,
) -> tuple[ProposalSection, list[str]]:
    logs: list[str] = []
    body = section.content or ""
    cleaned, asset_logs = strip_invented_asset_promises(body)
    logs.extend(asset_logs)
    if word_ceiling and word_ceiling > 0:
        cleaned, removed = _trim_to_word_ceiling(cleaned, word_ceiling)
        if removed:
            logs.append(
                f"ralph:trim-overshoot:{section.id}: removed ~{removed} words "
                f"(over wordTarget×{_SECTION_OVERSHOOT_RATIO:.2f})"
            )
    if cleaned == (section.content or ""):
        return section, logs
    return section.model_copy(update={"content": cleaned}), logs


def apply_ralph_to_draft(
    draft: ProposalDraft,
    *,
    page_limit: int | None,
    rfp_text: str | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Strip invented assets; only trim sections that wildly overshoot wordTarget.

    Does NOT rescale the whole manuscript to a page budget after generation —
    page fit belongs to draft-time ``allocate_word_budget``.
    Never soft-trims the Cost/Fees/Budget tab — fee tables live at the end and
    must survive length chops.
    """
    del rfp_text  # page_limit already resolved by caller when available
    _ = resolve_page_limit(page_limit, None)  # keep API stable; unused for hard chops
    logs: list[str] = []
    new_sections: list[ProposalSection] = []

    from app.services.proposal_budget_content import (
        find_budget_section_index,
        section_is_budgetish,
    )

    budget_idx = find_budget_section_index(draft.sections)

    for i, section in enumerate(draft.sections):
        ceiling: int | None = None
        protect_budget = i == budget_idx or section_is_budgetish(section)
        wt = section.word_target
        if (
            not protect_budget
            and isinstance(wt, int)
            and wt > 0
        ):
            soft = int(wt * _SECTION_OVERSHOOT_RATIO)
            if _word_count(section.content or "") > soft:
                ceiling = soft
        updated, section_logs = apply_ralph_to_section(section, word_ceiling=ceiling)
        new_sections.append(updated)
        logs.extend(section_logs)

    if not logs:
        return draft, []
    return draft.model_copy(update={"sections": new_sections}), logs


def inject_ralph_into_system_prompt(system: str) -> str:
    if "RALPH — RFP FIDELITY" in (system or ""):
        return system
    return f"{system.rstrip()}\n\n{RALPH_RFP_FIDELITY_RULES.strip()}\n"
