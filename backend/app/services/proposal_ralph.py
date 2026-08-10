"""Ralph — RFP fidelity controller (concise writing + no invented assets).

When the RFP states a page limit (form field or parsed from solicitation
text), Ralph hard-fits the manuscript to that budget after generation/Scan —
without inventing a default 12-page cap when none is stated.

Hard-fit order: strip invented assets → soft-trim wild wordTarget overshoots
→ trim longest *unprotected* tabs → only then lightly trim scored tabs if
still over. Identity / bios / case studies / budget are never hard-chopped
first; scored tabs keep a floor so important asks survive.
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
# trim that section before optional document-level hard-fit.
_SECTION_OVERSHOOT_RATIO = 1.25
# Scored / protected tabs keep at least this many words under hard-fit pressure.
_SCORED_HARD_FIT_FLOOR = 120
_UNPROTECTED_HARD_FIT_FLOOR = 60

RALPH_RFP_FIDELITY_RULES = """
## RALPH — RFP FIDELITY (mandatory)

You are writing under Ralph's RFP rules. Accuracy and concision beat volume.
ZERO REPETITION: never restate facts already owned by another section.

PAGE / LENGTH DISCIPLINE:
1. Obey wordTarget as a HARD CEILING, not a goal. Shorter is better when the
   RFP asks are answered. Do not pad or expand for length.
2. If THIS RFP states a page limit, plan dense copy that can fit that limit —
   write each section to its allocated wordTarget (do not dump extra pages
   "for the designer to cut"). If the RFP states no page limit, stay concise
   anyway — never invent length.
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
    r"process\s+map|flow\s+chart|wireframe|mockup|screenshot|"
    r"reporting\s+diagram|sample\s+report)[^\]]*\]",
    re.IGNORECASE,
)

_INVENTED_ASSET_CLAIM_RE = re.compile(
    r"(?im)^[^\n]*(?:attached\s+(?:is|are)|please\s+see|see\s+(?:the\s+)?(?:enclosed|attached)|"
    r"we\s+(?:will\s+)?(?:include|provide|attach)|included\s+(?:is|are))"
    r"[^\n]*(?:diagram|dashboard|infographic|org\s+chart|process\s+map|"
    r"reporting\s+(?:portal|dashboard|diagram)|sample\s+report\s+(?:graphic|diagram)|"
    r"visual\s+dashboard)[^\n]*$",
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
    """Manuscript word budget from RFP page limit (None when no limit stated)."""
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


def _section_eval_points(section: ProposalSection) -> float:
    for attr in ("evaluation_weight", "points"):
        value = getattr(section, attr, None)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_identity_or_portfolio_section(section: ProposalSection) -> bool:
    sid = section.id or ""
    if sid.startswith("section-1-"):
        return True
    if sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
        return True
    if sid.startswith("section-3-work-") and not sid.endswith("placeholder"):
        return True
    return False


def _is_hard_fit_protected(
    section: ProposalSection,
    *,
    budget_idx: int | None,
    index: int,
) -> bool:
    """Tabs that must not be first in line for hard page-fit chops."""
    from app.services.proposal_budget_content import section_is_budgetish

    if index == budget_idx or section_is_budgetish(section):
        return True
    if _is_identity_or_portfolio_section(section):
        return True
    if _section_eval_points(section) > 0:
        return True
    return False


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


def _draft_word_total(sections: list[ProposalSection]) -> int:
    return sum(_word_count(s.content or "") for s in sections if (s.content or "").strip())


def _hard_fit_sections_to_budget(
    sections: list[ProposalSection],
    *,
    budget_words: int,
    budget_idx: int | None,
) -> tuple[list[ProposalSection], list[str]]:
    """Trim longest eligible tabs until manuscript ≤ budget_words.

    Never deletes tabs. Never empties protected/scored content below floors.
    """
    logs: list[str] = []
    if budget_words <= 0:
        return sections, logs

    out = list(sections)
    if _draft_word_total(out) <= budget_words:
        return out, logs

    from app.services.proposal_budget_content import section_is_budgetish

    def _eligible(prefer_unprotected: bool) -> list[tuple[int, int]]:
        rows: list[tuple[int, int]] = []
        for i, section in enumerate(out):
            wc = _word_count(section.content or "")
            if wc <= 0:
                continue
            if _is_identity_or_portfolio_section(section):
                continue
            if i == budget_idx or section_is_budgetish(section):
                continue
            protected = _is_hard_fit_protected(
                section, budget_idx=budget_idx, index=i
            )
            if prefer_unprotected and protected:
                continue
            if not prefer_unprotected and not protected:
                continue
            floor = (
                _SCORED_HARD_FIT_FLOOR
                if protected
                else _UNPROTECTED_HARD_FIT_FLOOR
            )
            if wc <= floor:
                continue
            rows.append((i, wc))
        rows.sort(key=lambda row: row[1], reverse=True)
        return rows

    for prefer_unprotected, label in ((True, "unprotected"), (False, "scored")):
        while _draft_word_total(out) > budget_words:
            candidates = _eligible(prefer_unprotected)
            if not candidates:
                break
            idx, wc = candidates[0]
            section = out[idx]
            protected = _is_hard_fit_protected(
                section, budget_idx=budget_idx, index=idx
            )
            floor = _SCORED_HARD_FIT_FLOOR if protected else _UNPROTECTED_HARD_FIT_FLOOR
            overflow = _draft_word_total(out) - budget_words
            target = max(floor, wc - max(40, min(overflow, int(wc * 0.35))))
            if target >= wc:
                break
            trimmed, removed = _trim_to_word_ceiling(section.content or "", target)
            if removed <= 0:
                break
            out[idx] = section.model_copy(update={"content": trimmed})
            logs.append(
                f"ralph:page-hard-fit:{label}:{section.id}: removed ~{removed} words "
                f"(RFP page budget)"
            )

    final_total = _draft_word_total(out)
    if final_total > budget_words:
        logs.append(
            f"ralph:page-hard-fit:still-over:{final_total}>{budget_words} "
            f"(protected content preserved — designer may still need light cuts)"
        )
    else:
        logs.append(
            f"ralph:page-hard-fit:ok:{final_total}w within budget {budget_words}w"
        )
    return out, logs


def apply_ralph_to_draft(
    draft: ProposalDraft,
    *,
    page_limit: int | None,
    rfp_text: str | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Anti-invention scrub + optional hard page-fit when the RFP states a limit.

    ``page_limit`` / ``rfp_text`` are resolved via ``resolve_page_limit`` —
    no default page cap is invented when the solicitation is silent.
    """
    effective_limit = resolve_page_limit(page_limit, rfp_text)
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
        if not protect_budget and isinstance(wt, int) and wt > 0:
            soft = int(wt * _SECTION_OVERSHOOT_RATIO)
            if _word_count(section.content or "") > soft:
                ceiling = soft
        updated, section_logs = apply_ralph_to_section(section, word_ceiling=ceiling)
        new_sections.append(updated)
        logs.extend(section_logs)

    budget_words = ralph_document_word_budget(effective_limit)
    if budget_words is not None:
        if effective_limit:
            logs.insert(
                0,
                f"ralph:page-limit:{effective_limit} "
                f"(budget {budget_words}w @ {WORDS_PER_PAGE} w/page)",
            )
        new_sections, fit_logs = _hard_fit_sections_to_budget(
            new_sections,
            budget_words=budget_words,
            budget_idx=budget_idx,
        )
        logs.extend(fit_logs)

    if not logs:
        return draft, []
    return draft.model_copy(update={"sections": new_sections}), logs


def inject_ralph_into_system_prompt(system: str) -> str:
    if "RALPH — RFP FIDELITY" in (system or ""):
        return system
    return f"{system.rstrip()}\n\n{RALPH_RFP_FIDELITY_RULES.strip()}\n"
