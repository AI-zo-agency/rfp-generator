"""Detect submission blockers (grammar, pronouns, cross-section consistency). No static rewrites."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache

# Detection patterns only
_ACROSS_WE_RE = re.compile(r"\bacross\s+we\b", re.IGNORECASE)
_OF_WE_RE = re.compile(r"\bof\s+we\b", re.IGNORECASE)
_WERE_AND_IS_RE = re.compile(
    r"\bWe were[^.]{0,120}?,\s*and\s+is\b",
    re.IGNORECASE,
)
_SUBCONTRACTOR_IN_BUDGET_RE = re.compile(
    r"subcontractor|translation partner|certified transl",
    re.IGNORECASE,
)
_DENY_SUBCONTRACTORS_RE = re.compile(
    r"(?:do not propose any subcontractors|no subcontractors(?:\s+for this engagement)?|"
    r"self-perform all work[^.]*subcontractor)",
    re.IGNORECASE,
)

GRAMMAR_GLITCH_RE = re.compile(
    r"\bbeing we\b|\bwe we\b|\bthe the\b|"
    r"\bof we\b|\bacross we\b|"
    r"\bWe were [^.]{0,120}, and is\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SubmissionBlocker:
    section_id: str
    section_title: str
    category: str  # grammar | pronoun | consistency
    message: str
    excerpt: str


def has_grammar_glitches(content: str) -> bool:
    if not content.strip():
        return False
    return bool(GRAMMAR_GLITCH_RE.search(content))


def budget_mentions_subcontractors(
    budget: ProposalBudget | None,
    draft: ProposalDraft | None,
) -> bool:
    parts: list[str] = []
    if budget:
        for item in budget.line_items:
            parts.append(item.description or "")
            parts.append(item.category or "")
        for field in (
            budget.fee_structure,
            budget.qualifying_language,
            budget.scope_summary,
            budget.option_term_notes,
        ):
            if field:
                parts.append(field)
    if draft:
        for section in draft.sections:
            title = (section.title or "").casefold()
            if any(
                token in title
                for token in ("cost", "budget", "cultural", "linguistic", "reimbursable")
            ):
                parts.append(section.content or "")
    return bool(_SUBCONTRACTOR_IN_BUDGET_RE.search("\n".join(parts)))


def deny_subcontractors_claimed(content: str) -> bool:
    return bool(_DENY_SUBCONTRACTORS_RE.search(content))


def _excerpt_around(pattern: re.Pattern[str], content: str, window: int = 50) -> str:
    match = pattern.search(content)
    if not match:
        return ""
    start = max(0, match.start() - window)
    end = min(len(content), match.end() + window)
    return content[start:end].strip()


def scan_submission_blockers(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None = None,
) -> list[SubmissionBlocker]:
    """Find the three pre-submission defect classes senior editor must fix."""
    blockers: list[SubmissionBlocker] = []
    budget = research.budget if research else None
    has_subs = budget_mentions_subcontractors(budget, draft)

    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue

        if _WERE_AND_IS_RE.search(content):
            blockers.append(
                SubmissionBlocker(
                    section_id=section.id,
                    section_title=section.title,
                    category="grammar",
                    message=(
                        "Subject-verb disagreement after 'We were …' — use 'and are organized' "
                        "or rephrase (e.g. 'organized as an S-Corp/LLC')."
                    ),
                    excerpt=_excerpt_around(_WERE_AND_IS_RE, content),
                )
            )

        if _OF_WE_RE.search(content) or _ACROSS_WE_RE.search(content):
            blockers.append(
                SubmissionBlocker(
                    section_id=section.id,
                    section_title=section.title,
                    category="pronoun",
                    message=(
                        "Malformed possessive: 'of we' / 'across we' — use 'zö agency', "
                        "'the firm', or 'our studio'."
                    ),
                    excerpt=_excerpt_around(_OF_WE_RE, content)
                    or _excerpt_around(_ACROSS_WE_RE, content),
                )
            )

        title_lower = section.title.casefold()
        is_company_bg = "company background" in title_lower or "company overview" in title_lower
        if has_subs and deny_subcontractors_claimed(content) and (
            is_company_bg or "self-perform all work" in content.casefold()
        ):
            blockers.append(
                SubmissionBlocker(
                    section_id=section.id,
                    section_title=section.title,
                    category="consistency",
                    message=(
                        "Company narrative contradicts cost proposal / cultural competency: "
                        "do NOT claim 'no subcontractors' when translation partners are budgeted."
                    ),
                    excerpt=content[:280],
                )
            )

    return blockers


def sections_with_submission_blockers(
    draft: ProposalDraft,
    research: ProposalResearchCache | None = None,
) -> set[str]:
    return {b.section_id for b in scan_submission_blockers(draft=draft, research=research)}


def related_section_excerpts(
    draft: ProposalDraft,
    *,
    keywords: tuple[str, ...],
    max_chars: int = 1200,
) -> list[tuple[str, str]]:
    """Pull excerpts from other sections for cross-reference in senior-editor repair."""
    excerpts: list[tuple[str, str]] = []
    for section in draft.sections:
        title_lower = (section.title or "").casefold()
        content = (section.content or "").strip()
        if not content:
            continue
        if any(kw in title_lower or kw in content.casefold() for kw in keywords):
            excerpts.append((section.title, content[:max_chars]))
    return excerpts


def build_submission_repair_brief(
    blockers: list[SubmissionBlocker],
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> str:
    """Surgical repair instructions for the Section Repair agent (not static substitution)."""
    lines = [
        "SUBMISSION POLISH — Senior editor flagged defects that MUST be fixed before submission.",
        "Rewrite ONLY the broken sentences/phrases. Preserve all other prose, structure, headings, and length.",
        "Use KB tools if you need entity facts (S-Corp/LLC, ownership, team roles). Do not invent facts.",
        "",
        "## Defects to fix",
    ]
    for index, blocker in enumerate(blockers, start=1):
        lines.append(
            f"{index}. **[{blocker.category}]** {blocker.message}"
        )
        if blocker.excerpt:
            lines.append(f'   Broken excerpt: "{blocker.excerpt}"')

    if any(b.category == "consistency" for b in blockers):
        lines.extend(["", "## Cross-section context (these sections are CORRECT — align to them)"])
        budget = research.budget if research else None
        if budget:
            for item in budget.line_items:
                blob = f"{item.category} {item.description}"
                if _SUBCONTRACTOR_IN_BUDGET_RE.search(blob):
                    amt = f" — ${item.extended:,.0f}" if item.extended else ""
                    lines.append(f"- Budget line: {item.description}{amt}")
        for title, excerpt in related_section_excerpts(
            draft,
            keywords=("cultural", "linguistic", "cost", "reimbursable", "translation"),
        ):
            lines.append(f"- **{title}:** {excerpt[:500]}…" if len(excerpt) > 500 else f"- **{title}:** {excerpt}")

        lines.extend(
            [
                "",
                "Required framing: zö self-performs all marketing and communications work. "
                "Certified translation partners for Pacific Islander languages are engaged as "
                "documented in the cost proposal and cultural competency section — NOT a contradiction.",
            ]
        )

    if any(b.category == "grammar" for b in blockers):
        lines.append(
            "\nGrammar: compound subject 'We were established …' requires plural verb — "
            "'and are organized' not 'and is organized'."
        )

    if any(b.category == "pronoun" for b in blockers):
        lines.append(
            "\nPronouns: never use 'we' as a possessive noun ('of we', 'across we'). "
            "Use 'zö agency', 'the firm', 'our studio', or 'our team'."
        )

    return "\n".join(lines)


# Back-compat alias for senior-editor injection
def scan_grammar_issues(*, draft: ProposalDraft) -> list[dict[str, str]]:
    return [
        {
            "sectionId": b.section_id,
            "sectionTitle": b.section_title,
            "issue": f"[{b.category}] {b.message}",
        }
        for b in scan_submission_blockers(draft=draft)
    ]
