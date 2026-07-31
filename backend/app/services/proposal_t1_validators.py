"""Deterministic T1 manuscript gates: note leaks and truncation artifacts.

Pure detection only — no LLM, no pipeline behaviour change until wired later.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, TypedDict

from app.models.proposal import ProposalDraft, ProposalSection

logger = logging.getLogger(__name__)

T1Severity = Literal["warning", "critical"]
T1Category = Literal[
    "note_leak", "truncation", "placeholder", "budget", "duplication"
]


class T1Finding(TypedDict):
    code: str
    severity: T1Severity
    category: T1Category
    section_id: str | None
    section_title: str | None
    message: str
    excerpt: str | None
    blocker: bool


# --- Internal note leak patterns -------------------------------------------------

_FLAG_FOR_RE = re.compile(r"\[FLAG\s+FOR\b[^\]]*\]", re.IGNORECASE)

# Whole-word TODO / FIXME / XXX — avoid matching inside ordinary words.
_TODO_FIXME_XXX_RE = re.compile(r"\b(?:TODO|FIXME|XXX)\b")

# Bracketed production / ops notes (not VERIFY / MANUAL FILL / DESIGNER NOTE).
_PRODUCTION_NOTE_RE = re.compile(
    r"\[(?:INTERNAL(?:\s+NOTE)?|FOR\s+[A-Z][A-Z0-9_\- ]{1,40})\s*:[^\]]*\]",
    re.IGNORECASE,
)

# Allowed bracket tags that must never be treated as leaks.
_ALLOWED_BRACKET_TAG_RE = re.compile(
    r"\[(?:VERIFY|MANUAL\s+FILL|DESIGNER\s+NOTE)\b[^\]]*\]",
    re.IGNORECASE,
)

# --- Truncation patterns ---------------------------------------------------------

# Incomplete currency: `$325,242.` or `($325,242.` (0–1 decimal digits, not well-formed .XX)
_INCOMPLETE_CURRENCY_RE = re.compile(
    r"(?:\(\s*)?\$\d{1,3}(?:,\d{3})+\.(?:\d{0,1})?(?!\d)"
)

# Repeated token near currency: "66. 66 (" or ".66. 66 ("
_REPEATED_TOKEN_CURRENCY_TAIL_RE = re.compile(
    r"(\d{1,3})\.\s+\1\s*\(",
)

# Mid-sentence: final non-empty line lacking terminal punctuation.
_TERMINAL_PUNCT_RE = re.compile(r"[.!?…:]\s*$")
_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_BULLET_LINE_RE = re.compile(r"^\s*[-*+]\s+\S")
_NUMBERED_LIST_RE = re.compile(r"^\s*\d+[.)]\s+\S")


def _excerpt(text: str, match: re.Match[str] | None = None, *, limit: int = 120) -> str:
    if match is not None:
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        snippet = text[start:end].strip()
    else:
        snippet = text.strip()
    if len(snippet) > limit:
        return snippet[: limit - 1] + "…"
    return snippet


def _finding(
    *,
    code: str,
    category: T1Category,
    section: ProposalSection,
    message: str,
    excerpt: str | None,
    severity: T1Severity = "critical",
    blocker: bool = True,
) -> T1Finding:
    return T1Finding(
        code=code,
        severity=severity,
        category=category,
        section_id=section.id,
        section_title=section.title,
        message=message,
        excerpt=excerpt,
        blocker=blocker,
    )


def _content_without_allowed_tags(content: str) -> str:
    """Mask VERIFY / MANUAL FILL / DESIGNER NOTE so bracket scans ignore them."""
    return _ALLOWED_BRACKET_TAG_RE.sub(" ", content)


def scan_internal_note_leaks(draft: ProposalDraft) -> list[T1Finding]:
    """Detect shipped internal production notes in section content."""
    findings: list[T1Finding] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue

        for match in _FLAG_FOR_RE.finditer(content):
            findings.append(
                _finding(
                    code="t1.note_leak.flag_for",
                    category="note_leak",
                    section=section,
                    message="Internal [FLAG FOR ...] note leaked into section content",
                    excerpt=_excerpt(content, match),
                )
            )

        # Scan TODO/FIXME/XXX on content with allowed tags masked (tags shouldn't
        # contain those tokens, but keep scan simple on raw text).
        for match in _TODO_FIXME_XXX_RE.finditer(content):
            findings.append(
                _finding(
                    code="t1.note_leak.todo_marker",
                    category="note_leak",
                    section=section,
                    message=f"Internal marker {match.group(0)!r} leaked into section content",
                    excerpt=_excerpt(content, match),
                )
            )

        # Production notes: scan after removing allowed tags so VERIFY/MANUAL FILL
        # never trip INTERNAL/FOR patterns.
        masked = _content_without_allowed_tags(content)
        for match in _PRODUCTION_NOTE_RE.finditer(masked):
            # Map match back approximately via matched text search in original.
            tag_text = match.group(0)
            findings.append(
                _finding(
                    code="t1.note_leak.production_note",
                    category="note_leak",
                    section=section,
                    message="Bracketed internal production note leaked into section content",
                    excerpt=_excerpt(tag_text),
                )
            )

    return findings


def _final_nonempty_line(content: str) -> str | None:
    for line in reversed(content.splitlines()):
        if line.strip():
            return line
    return None


def _is_structural_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _HEADING_LINE_RE.match(stripped):
        return True
    if _TABLE_ROW_RE.match(stripped):
        return True
    if _BULLET_LINE_RE.match(stripped):
        return True
    if _NUMBERED_LIST_RE.match(stripped):
        return True
    return False


def _has_incomplete_currency_fragment(content: str) -> re.Match[str] | None:
    """Detect truncated currency like `$325,242.` (missing cents) or open `($325,242.`."""
    # Well-formed $X,XXX.XX is excluded: pattern allows at most one digit after `.`.
    return _INCOMPLETE_CURRENCY_RE.search(content)


def _unbalanced_parens(content: str) -> bool:
    depth = 0
    for ch in content:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0


def _unbalanced_square_brackets(content: str) -> bool:
    """Flag only unclosed `[` that are not complete known/allowed tags.

    Allowed/complete tags (VERIFY, MANUAL FILL, DESIGNER NOTE, FLAG FOR, INTERNAL,
    FOR NAME:) are stripped first; remaining bare `[` without `]` is a truncation
    signal. Complete unknown tags that close are ignored.
    """
    # Remove complete bracket spans (any [...]) — leftover `[` implies truncation.
    stripped = re.sub(r"\[[^\]]*\]", "", content)
    return "[" in stripped


def scan_truncation_artifacts(draft: ProposalDraft) -> list[T1Finding]:
    """Detect mid-sentence cutoffs, currency fragments, and unbalanced delimiters."""
    findings: list[T1Finding] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue

        # 1) Repeated-token currency tail (e.g. "66. 66 (")
        for match in _REPEATED_TOKEN_CURRENCY_TAIL_RE.finditer(content):
            findings.append(
                _finding(
                    code="t1.truncation.repeated_token_tail",
                    category="truncation",
                    section=section,
                    message="Repeated numeric token near currency suggests truncation",
                    excerpt=_excerpt(content, match),
                )
            )

        # 2) Incomplete / trailing currency fragments
        currency_match = _has_incomplete_currency_fragment(content)
        if currency_match is not None:
            findings.append(
                _finding(
                    code="t1.truncation.currency_fragment",
                    category="truncation",
                    section=section,
                    message="Incomplete currency/numeric fragment suggests truncation",
                    excerpt=_excerpt(content, currency_match),
                )
            )

        # 3) Mid-sentence cutoff on final non-empty line
        # Ignore trailing handoff tags ([MANUAL FILL]/[DESIGNER NOTE]/[VERIFY]) so
        # process markers do not look like truncated prose.
        from app.services.proposal_rfp_optional_claim_scrub import (
            strip_handoff_tags_for_scan,
        )

        scan_body = strip_handoff_tags_for_scan(content)
        last = _final_nonempty_line(scan_body if scan_body else content)
        if last is not None and not _is_structural_line(last):
            if not _TERMINAL_PUNCT_RE.search(last.rstrip()):
                findings.append(
                    _finding(
                        code="t1.truncation.mid_sentence_cutoff",
                        category="truncation",
                        section=section,
                        message="Section ends mid-sentence without terminal punctuation",
                        excerpt=_excerpt(last),
                    )
                )

        # 4) Unbalanced parentheses
        if _unbalanced_parens(content):
            findings.append(
                _finding(
                    code="t1.truncation.unbalanced_parens",
                    category="truncation",
                    section=section,
                    message="Unbalanced parentheses suggest truncation",
                    excerpt=_excerpt(content[-160:] if len(content) > 160 else content),
                )
            )

        # 5) Unclosed square brackets (after removing complete tags)
        if _unbalanced_square_brackets(content):
            findings.append(
                _finding(
                    code="t1.truncation.unbalanced_brackets",
                    category="truncation",
                    section=section,
                    message="Unclosed square bracket suggests truncation",
                    excerpt=_excerpt(content[-160:] if len(content) > 160 else content),
                )
            )

    return findings


def scan_all_t1(draft: ProposalDraft) -> list[T1Finding]:
    """Run all T1 deterministic scanners and return combined findings."""
    findings = scan_internal_note_leaks(draft) + scan_truncation_artifacts(draft)
    blockers = [f for f in findings if f["blocker"]]
    if blockers:
        section_ids = sorted(
            {f["section_id"] for f in blockers if f.get("section_id")}
        )
        logger.info(
            "t1_blockers_found count=%s section_ids=%s codes=%s",
            len(blockers),
            section_ids,
            sorted({f["code"] for f in blockers}),
        )
    return findings


def t1_findings_as_blocker_messages(findings: list[T1Finding]) -> list[str]:
    """Format blocker findings as pipeline-ready message strings."""
    messages: list[str] = []
    for f in findings:
        if not f.get("blocker"):
            continue
        loc = f.get("section_id") or f.get("section_title") or "unknown"
        messages.append(f"[T1:{f['code']}] section={loc}: {f['message']}")
    return messages
