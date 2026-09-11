"""Shared formatting rules for ledger AI briefs and chat answers."""

# Used by Agency / QuickBooks / Teamwork / iWorker brief generators.
BRIEF_FORMAT = (
    "Format the brief as compact markdown for a fast owner skim. "
    "REQUIRED: each distinct issue or action is its own markdown bullet line "
    "starting with '- ' (hyphen-space). Never a wall of paragraphs. "
    "**bold** key figures and client/project names; *italic* only for light emphasis. "
    "No headings, no numbered outlines, no code fences. "
    "US English. Still reuse figures verbatim or omit them."
)

# Per-card AI notes sit under a headline already — keep them short.
NOTE_FORMAT = (
    "One or two short sentences; **bold** figures and names when useful. "
    "No bullet lists in notes (the card already is the list item)."
)

# Follow-up chat in the same drawer.
CHAT_FORMAT = (
    "Answer in compact markdown: short bullets when listing multiple items, "
    "**bold** key figures and names, *italic* sparingly. "
    "No headings or code fences. Keep it short."
)
