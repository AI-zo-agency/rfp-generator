"""Shared vocabulary for chat intent routing.

Edit verbs were previously enumerated by hand in three places that drifted apart:
``_EDIT_INTENT_RE`` and ``_FOLLOW_WITH_MUTATE_RE`` in ``proposal_section_editor``
and ``_ADD_CASE_STUDY_INTENT_RE`` in ``proposal_chat_structure``. ``create``
appeared in one alternation of the last and was missing from its sibling, and
common copy-editing verbs (``tighten``, ``trim``, ``reword``) appeared in none of
them.

An unrecognised verb matches neither the edit nor the advisory pattern, and
``_wants_section_edit`` ends at "safe default: answer in chat" — so a missing verb
silently turns an edit request into an essay. Measured accuracy before this module
existed was 27/39 on tests/fixtures/chat_routing_cases.json.

Add a verb here, not to a regex.
"""

from __future__ import annotations

#: Verbs that mean "change the draft text".
EDIT_VERBS: tuple[str, ...] = (
    # Original list.
    "change", "fix", "update", "rewrite", "revise", "edit", "improve",
    "shorten", "lengthen", "remove", "replace", "fill", "patch", "insert",
    "delete", "correct", "align", "apply", "resolve", "swap", "redraft",
    "regenerate",
    # Copy-editing verbs whose absence routed real edit requests to advisory.
    "tighten", "trim", "cut", "reword", "rephrase", "polish", "expand",
    "condense", "simplify", "strengthen", "reorder", "restructure",
    "shorten up", "punch up", "clean up", "tone down", "flesh out",
)

#: Verbs that mean "create a new sidebar section/tab/bio/case study".
ADD_VERBS: tuple[str, ...] = (
    "add", "create", "insert", "include", "make", "put", "append",
)


def verb_alternation(verbs: tuple[str, ...]) -> str:
    """Regex alternation for `verbs`, longest first so multi-word forms win.

    Without the length sort, "clean" would match before "clean up" and leave the
    particle dangling.
    """
    escaped = sorted((v.replace(" ", r"\s+") for v in verbs), key=len, reverse=True)
    return "|".join(escaped)


#: Interrogative openers that make a message a question about the draft rather
#: than an instruction to change it, even when it contains an edit verb
#: ("does the budget cut affect this?", "which sections need trimming?").
#: Deliberately excludes request forms — "can you shorten this?" is an edit.
QUESTION_OPENERS = (
    "what", "which", "why", "who", "when", "where",
    "does", "do", "did", "is", "are", "was", "were",
    "should", "how many", "how much", "how long",
)
