"""Surgical, span-level section edits for Complete & Clean.

Complete & Clean must fix the *specific* wrong thing (a fabricated fact, a wrong
title, a contradiction) by patching only that span and leaving the rest of the
section byte-for-byte intact — NOT by regenerating the whole section body. A
whole-section LLM rewrite to fix one sentence is exactly what let a 2,300-word
tab come back emptied / stubbed. This module is the shared contract and applier
for that patch model, reused by every scan fix pass.

The LLM returns a list of {find, replace} edits. `find` must be copied verbatim
from the current section; we apply each as a literal first-occurrence
replacement. Anything not matched is never touched, and a patch that would delete
too much is refused outright.
"""

from __future__ import annotations

from typing import Any

# Drop into a fix pass's system prompt in place of a "return the full section"
# instruction. Keeps the model in patch mode.
TARGETED_EDIT_CONTRACT = (
    "Fix ONLY the specific wrong text. Do NOT rewrite, reorder, summarize, or "
    "re-format the section, and do NOT return the whole section. Return the "
    "smallest set of exact-text replacements that resolve the issue.\n"
    "Return JSON: {\"edits\": [{\"find\": \"<exact text copied verbatim from the "
    "current section — one sentence, table cell, phrase, or number; long enough "
    "to occur exactly once>\", \"replace\": \"<corrected text, same span only>\"}], "
    "\"changed\": true/false, \"notes\": \"one line\"}\n"
    "Rules for every edit:\n"
    "- `find` MUST appear character-for-character in the current section (copy it, "
    "do not paraphrase). If you cannot quote the exact text, do not emit that edit.\n"
    "- `replace` covers the SAME span only — never fold in neighbouring sentences "
    "and never expand a short fix into a paragraph.\n"
    "- Never invent numbers, dates, names, clients, or dollars. To remove a "
    "fabrication, replace it with corrected verbatim text, an empty string, or one "
    "precise [VERIFY: specific field] — never a new invented fact.\n"
    "- If the issue cannot be fixed with minimal edits without inventing facts, "
    "return \"edits\": [] and it will be flagged for a human."
)


def parse_targeted_edits(raw: Any) -> list[tuple[str, str]]:
    """Pull (find, replace) pairs out of the model's JSON reply. Tolerant of key aliases."""
    if not isinstance(raw, dict):
        return []
    rows = raw.get("edits") or raw.get("patches") or []
    if not isinstance(rows, list):
        return []
    edits: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        find = row.get("find")
        if find is None:
            find = row.get("original") or row.get("from") or row.get("old")
        replace = row.get("replace")
        if replace is None:
            replace = row.get("replacement") or row.get("to") or row.get("new") or ""
        if not isinstance(find, str) or not isinstance(replace, str):
            continue
        if not find.strip():
            continue
        edits.append((find, replace))
    return edits


def apply_targeted_edits(
    body: str,
    edits: list[tuple[str, str]],
    *,
    max_delete_ratio: float = 0.34,
    max_expand_chars: int = 400,
    delete_guard_min_chars: int = 600,
) -> tuple[str, int, bool, str]:
    """Apply (find, replace) edits as literal first-occurrence replacements.

    Returns ``(new_body, applied_count, changed, reason)``. Untouched text is kept
    exactly. Safeguards:
      - an edit whose ``find`` is not present verbatim is skipped (never a fuzzy
        or whole-section fallback);
      - a single ``replace`` far longer than its ``find`` is skipped (guards
        against a "patch" that smuggles in an invented paragraph);
      - the result is never allowed to become empty;
      - for a SUBSTANTIAL section (≥ ``delete_guard_min_chars``), a patch that
        removed more than ``max_delete_ratio`` of it is refused — that shape is a
        mass wipe disguised as edits. Short sections skip this ratio check: a
        single verbatim fix that trims a fabricated clause is legitimately a large
        fraction of a one-sentence tab and must be allowed.
    """
    original = body or ""
    if not original.strip() or not edits:
        return original, 0, False, "no edits"

    working = original
    applied = 0
    for find, replace in edits:
        if not find or find not in working:
            continue
        if len(replace) > len(find) + max_expand_chars:
            # A minimal fix never balloons a short span into a long passage.
            continue
        working = working.replace(find, replace, 1)
        applied += 1

    if applied == 0 or working == original:
        return original, 0, False, "no matching spans"
    if not working.strip():
        return original, 0, False, "patch emptied the section — refused"
    if (
        len(original.strip()) >= delete_guard_min_chars
        and len(working.strip()) < len(original.strip()) * (1 - max_delete_ratio)
    ):
        return original, 0, False, "patch removed too much — refused"
    return working, applied, True, "patched"


def _preserved_fraction(original: str, proposed: str) -> float:
    """Fraction of the original's substantive segments still present verbatim in
    the proposed body. Segment = a stripped line of ≥ 20 chars (falls back to
    sentence-ish splits). Robust on repetitive text where difflib's autojunk
    heuristic collapses; cheap because there are only dozens of segments.
    """
    def _segments(text: str) -> list[str]:
        segs: list[str] = []
        for line in (text or "").splitlines():
            s = line.strip()
            if len(s) >= 20:
                segs.append(s)
        if not segs:
            # No line breaks — split on sentence boundaries so a one-paragraph tab
            # still yields comparable chunks.
            for piece in (text or "").replace("! ", ". ").replace("? ", ". ").split(". "):
                s = piece.strip()
                if len(s) >= 20:
                    segs.append(s)
        return segs

    orig_segs = _segments(original)
    if not orig_segs:
        return 1.0 if (original or "").strip() in (proposed or "") else 0.0
    hay = proposed or ""
    kept = sum(1 for seg in orig_segs if seg in hay)
    return kept / len(orig_segs)


def enforce_localized_edit(
    original: str,
    proposed: str,
    *,
    min_len_for_guard: int = 600,
    min_preserved: float = 0.4,
) -> tuple[str, bool, str]:
    """Backstop for LLM fix passes that still return a whole rewritten section.

    Complete & Clean must patch, not rewrite. Where a pass is not yet on the
    span-patch contract, this gate refuses a proposed body that is effectively a
    wholesale replacement of a SUBSTANTIAL good section (it preserves less than
    ``min_preserved`` of the original's segments verbatim) and keeps the original
    instead. Short or already-thin sections skip the guard — completing or
    grounding a stubby tab is a legitimate large change. Returns
    ``(body_to_use, accepted, reason)``.
    """
    orig = original or ""
    prop = (proposed or "").strip()
    if not prop or prop == orig.strip():
        return orig, False, "no change"
    # Below the substantial-content threshold, allow the change (thin tab fill).
    if len(orig.strip()) < min_len_for_guard:
        return prop, True, "accepted (short section)"
    preserved = _preserved_fraction(orig, prop)
    if preserved < min_preserved:
        return (
            orig,
            False,
            f"refused wholesale rewrite of a good section "
            f"(kept {preserved:.0%} < {min_preserved:.0%} of it) — patch in place instead",
        )
    return prop, True, f"accepted localized edit (kept {preserved:.0%})"
