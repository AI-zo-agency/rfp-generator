"""Ordered full-proposal manuscript for export (matches workspace section order)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.proposal import ProposalDraft, ProposalSection

SECTION_1_ID_ORDER: tuple[str, ...] = (
    "section-1-who-we-are",
    "section-1-org-structure",
    "section-1-business-info",
    "section-1-certifications",
    "section-1-insurance",
    "section-1-company-overview",
)

_PLACEHOLDER_IDS = frozenset(
    {
        "section-2-bio-placeholder",
        "section-3-work-placeholder",
    }
)

_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-–—]+\|[\s|:\-–—]+\|?\s*$")


def _parse_title_major_minor(title: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d+)\.(\d+)", title or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return 999, 999


def manuscript_rank(section: "ProposalSection") -> tuple:
    sid = section.id
    major, minor = _parse_title_major_minor(section.title)

    if sid.startswith("section-1-"):
        try:
            idx = SECTION_1_ID_ORDER.index(sid)  # type: ignore[arg-type]
        except ValueError:
            idx = 40 + minor
        return (1, idx, minor, sid)
    if sid.startswith("section-2-bio-") or sid == "section-2-team-overview":
        return (2, minor, 0, sid)
    if sid.startswith("section-3-work-") or sid == "section-3-our-work":
        return (3, minor, 0, sid)
    if sid.startswith("section-4-"):
        return (4, major, minor, sid)
    if sid.startswith("section-5-"):
        return (5, major, minor, sid)
    if section.source == "rfp" or sid.startswith("rfp-"):
        return (6, major, minor, sid)
    return (7, major, minor, sid)


def manuscript_sections_for_export(sections: list["ProposalSection"]) -> list["ProposalSection"]:
    """All non-placeholder sections with body text, in proposal reading order."""
    out: list[ProposalSection] = []
    for section in sections:
        if section.id in _PLACEHOLDER_IDS:
            continue
        if not (section.content or "").strip():
            continue
        out.append(section)
    # Workspace order is the reading order (static 1–3 stay grouped; intelligence
    # tabs follow the RFP sequence). Do not re-rank and bury Cover Letter / Cost.
    return out


def strip_evidence_citation_markers(text: str) -> str:
    """Remove internal evidence markers from client-facing prose.

    Handles single markers ([E1], **[E14]**), comma lists ([E12, E13, E14]),
    and orphaned 'References:' lines that only listed evidence ids.
    """
    if not text:
        return text
    cleaned = text
    # Comma / semicolon lists inside one bracket: [E12, E13, E14, E15]
    cleaned = re.sub(
        r"\s*\*{0,2}\[\s*E\d+(?:\s*[,;]\s*E\d+)+\s*\]\*{0,2}",
        "",
        cleaned,
        flags=re.I,
    )
    # Single markers: [E1], **[E14]**
    cleaned = re.sub(r"\s*\*{0,2}\[E\d+\]\*{0,2}", "", cleaned, flags=re.I)
    # Orphaned references lines (with or without leftover bold markers).
    cleaned = re.sub(
        r"(?im)^\s*\**\s*References?\s*\**\s*:?\s*\**\s*"
        r"(?:\[?\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]?)?\s*$",
        "",
        cleaned,
    )
    # Clean doubled spaces left by marker removal (keep newlines).
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


_FLAG_FOR_TAG_RE = re.compile(
    r"\[FLAG(?:\s+FOR\b)?[^\]]*\]",
    re.IGNORECASE,
)


def strip_internal_flag_tags(text: str) -> str:
    """Remove [FLAG FOR …] / [FLAG: …] internal handoff notes from saved draft bodies.

    Preserves [MANUAL FILL], [DESIGNER NOTE], and [VERIFY] — those may still be
    needed for designer / Sonja handoff. Export-time scrub uses strip_internal_handoff_tags
    which removes the full handoff set.
    """
    if not text:
        return text
    cleaned = _FLAG_FOR_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def strip_internal_pricing_flags(text: str) -> str:
    """Remove [PRICING FLAG: …] blocks — internal Sonja notes, not client prose."""
    if not text:
        return text
    cleaned = re.sub(r"(?is)\[PRICING FLAG:[^\]]*\]", "", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


# --- Additional authoring-time scrubs (DuPage-class defects) ---------------------
#
# These strip / convert artifacts observed shipping in real drafts that neither
# INTERNAL_HANDOFF_TAG_RE nor scrub_client_facing_section_artifacts previously
# caught. Each is deliberately narrow so it cannot silently eat legitimate prose.

# [REMOVE: ...], [NOTE: ...], [TODO: ...] etc. — inline authoring instructions
# that leaked into the DuPage Creative team roster ("[REMOVE: verify roster, not
# on approved team] Graphic Designer") and elsewhere. These are NEVER legitimate
# handoffs — MANUAL FILL / DESIGNER NOTE / VERIFY / FLAG cover the real cases.
_INLINE_INSTRUCTION_TAG_RE = re.compile(
    r"\[(?:REMOVE|NOTE|TODO|FIXME|INTERNAL|CONFIRM)\b[^\]]*\]",
    re.IGNORECASE,
)


def strip_inline_instruction_tags(text: str) -> str:
    """Remove [REMOVE:]/[NOTE:]/[TODO:]/[FIXME:]/[INTERNAL:]/[CONFIRM:] tags.

    Observed inline mid-sentence in the DuPage draft — never a legitimate
    handoff shape. Real handoffs use MANUAL FILL / DESIGNER NOTE / VERIFY / FLAG.
    """
    if not text:
        return text
    cleaned = _INLINE_INSTRUCTION_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


# Unresolved template tokens ({{budget.agency_revenue}}, {{budget.total_client_invoicing}}).
# The LLM emitted these as literal Jinja-style variables in the DuPage Bid Pricing
# section and no substitution step ever ran, so "monthly professional services fee
# of {{budget.agency_revenue}}" shipped verbatim. Do NOT silently delete — that
# hides the missing fee. Convert to a highly visible MANUAL FILL the writer must
# resolve before submission.
_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


def convert_unresolved_template_tokens(text: str) -> str:
    """Turn stray {{namespace.field}} tokens into visible MANUAL FILL handoffs."""
    if not text:
        return text

    def _repl(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        ns, _, field = raw.partition(".")
        hint = f"resolve {ns} token: {field}" if ns and field else f"resolve token: {raw}"
        return f"[MANUAL FILL: Sonja — {hint}]"

    return _TEMPLATE_TOKEN_RE.sub(_repl, text)


# Instruction-shaped blocks the LLM narrates into the proposal body instead of
# following — the Carson draft shipped a whole blockquote of the model's own
# rules ("ACTION REQUIRED BEFORE SUBMISSION, PASS/FAIL ITEM ... Do not invent
# contacts, organizations, or project details.") as ordinary client-facing
# prose. `_BARE_CONFIRMATION_LINE_RE` above only catches single lines that
# START with a trigger phrase; this catches whole paragraphs/blockquotes
# anywhere the trigger phrases appear.
#
# Detection markers (case-insensitive substring match against the collapsed
# block): any of these means the block is instruction-shaped prose, not
# authored content.
_INSTRUCTION_BLOCK_MARKERS: tuple[str, ...] = (
    "action required",
    "pass/fail",
    "cannot be submitted",
    "must be confirmed with",
    "do not invent",
    "note to sonja",
    "note to leadership",
)

# Routing keywords. A block that names one of these concrete RFP deliverables,
# or a person to confirm with, becomes a [MANUAL FILL: Sonja — …] tag. Checked
# with plain substring containment, not regex — per the module's own house
# rule, new regex is reserved for structural/detection work, not keyword
# tests. "forms" (not bare "form") avoids colliding with the design keyword
# "format".
_INSTRUCTION_MANUAL_FILL_KEYWORDS: tuple[str, ...] = (
    "reference",
    "attachment",
    "certification",
    "insurance",
    "signature",
    "w-9",
    "addendum",
    "forms",
    "licence",
    "license",
    "bond",
    "confirmed with",
    "sonja",
)

# A block that is about layout/imagery/presentation (and names no concrete
# deliverable above) becomes a [DESIGNER NOTE: …] tag instead.
_INSTRUCTION_DESIGNER_KEYWORDS: tuple[str, ...] = (
    "layout",
    "design",
    "image",
    "logo",
    "spread",
    "page",
    "format",
)

_INSTRUCTION_BLOCK_SPLIT_RE = re.compile(r"(\n[ \t]*\n+)")
_INSTRUCTION_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _instruction_block_matches(lowered_block: str) -> bool:
    if any(marker in lowered_block for marker in _INSTRUCTION_BLOCK_MARKERS):
        return True
    return "before submission" in lowered_block and "verify" in lowered_block


def _strip_instruction_line_prefix(line: str) -> str:
    """Strip leading '>' / '⚠' markers (and the whitespace around them)."""
    stripped = line.strip()
    while stripped[:1] in ("⚠", ">"):
        stripped = stripped[1:].strip()
    return stripped


def _classify_instruction_block(lowered_block: str) -> str | None:
    """Return "manual", "designer", or None (pure meta-commentary, drop it)."""
    if any(keyword in lowered_block for keyword in _INSTRUCTION_MANUAL_FILL_KEYWORDS):
        return "manual"
    if any(keyword in lowered_block for keyword in _INSTRUCTION_DESIGNER_KEYWORDS):
        return "designer"
    return None


def _instruction_sentence_is_actionable(lowered_sentence: str) -> bool:
    """A sentence survives only if it names a deliverable/person/presentation
    detail someone must act on. Pure narration about the model's own rules
    ("Do not invent contacts…", "This section is a pass/fail responsiveness
    requirement") has none of these keywords and is dropped."""
    return any(
        keyword in lowered_sentence
        for keyword in (*_INSTRUCTION_MANUAL_FILL_KEYWORDS, *_INSTRUCTION_DESIGNER_KEYWORDS)
    )


# --- Brief echo --------------------------------------------------------------
# The section writer receives a private brief (purpose, writerInstructions,
# successDefinition). When evidence is thin, the brief is the only substantive
# text in its context, so the model paraphrases it into the section body and a
# tab ships describing why it matters instead of answering it.
#
# convert_instruction_blocks catches known instruction PHRASINGS. This catches
# the general case — a sentence that is a restatement of THIS section's own
# brief — by comparing the body against the brief it was written from, which no
# fixed phrase list can do.
#
# Two independent signals must both fire, because either alone is a false
# positive generator: legitimate prose reuses the brief's vocabulary (that is
# the point of a brief), and "this section" is sometimes a fair transition.
_BRIEF_ECHO_META_MARKERS = (
    "this section",
    "this tab",
    "this narrative",
    "this response",
    "this subsection",
    "the purpose of this",
    "the goal of this",
    "the intent of this",
    "the evaluator",
    "evaluators will",
    "evaluators should",
    "the reader should",
    "the reviewer should",
    "should be able to",
    "is intended to demonstrate",
    "is designed to demonstrate",
    "will demonstrate to",
    "in this section we",
    "here we will",
    "below we will",
    "we will describe",
    "we will outline",
    "we will address",
)

# Sentences short enough that overlap is noise, and tags that are deliverables.
_BRIEF_ECHO_MIN_TOKENS = 5
_BRIEF_ECHO_CONTAINMENT = 0.55
_BRIEF_ECHO_PROTECTED_TAGS = ("[VERIFY", "[MANUAL FILL", "[DESIGNER NOTE", "[PRICING")


def _brief_tokens(text: str) -> set[str]:
    """Content words, lowercased. Plain string ops — no regex needed here."""
    out: set[str] = set()
    for raw in (text or "").casefold().split():
        word = raw.strip(".,;:!?()[]{}\"'`—–-")
        if len(word) > 3:
            out.add(word)
    return out


def _is_brief_echo(sentence: str, directive_tokens: list[set[str]]) -> bool:
    lowered = sentence.casefold()
    if not any(marker in lowered for marker in _BRIEF_ECHO_META_MARKERS):
        return False
    tokens = _brief_tokens(sentence)
    if len(tokens) < _BRIEF_ECHO_MIN_TOKENS:
        return False
    for directive in directive_tokens:
        if not directive:
            continue
        # Containment, not Jaccard: the brief field and the echo are rarely the
        # same length, but an echo is mostly made of the brief's own words.
        if len(tokens & directive) / len(tokens) >= _BRIEF_ECHO_CONTAINMENT:
            return True
    return False


def strip_brief_echo_sentences(text: str, directives: list[str]) -> str:
    """Drop sentences that restate the section's own private brief.

    ``directives`` are the META fields only — purpose, writerInstructions,
    successDefinition. Key messages are deliberately excluded: those ARE the
    content the section is supposed to make, so matching against them would
    delete the section's substance.

    Headings, table rows and handoff tags are never touched.
    """
    body = text or ""
    directive_tokens = [_brief_tokens(d) for d in directives if (d or "").strip()]
    if not body.strip() or not directive_tokens:
        return body

    out_lines: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith("|")
            or stripped.startswith(">")
            or any(tag in stripped for tag in _BRIEF_ECHO_PROTECTED_TAGS)
        ):
            out_lines.append(line)
            continue

        prefix = ""
        content = stripped
        for bullet in ("- ", "* ", "+ "):
            if content.startswith(bullet):
                prefix = line[: len(line) - len(line.lstrip())] + bullet
                content = content[len(bullet) :]
                break

        sentences = _INSTRUCTION_SENTENCE_SPLIT_RE.split(content)
        kept = [s for s in sentences if not _is_brief_echo(s, directive_tokens)]
        if len(kept) == len(sentences):
            out_lines.append(line)
            continue
        remainder = " ".join(s.strip() for s in kept if s.strip()).strip()
        if not remainder:
            # Whole line was brief echo — drop it rather than leave a stub bullet.
            continue
        if prefix:
            out_lines.append(f"{prefix}{remainder}")
        else:
            indent = line[: len(line) - len(line.lstrip())]
            out_lines.append(f"{indent}{remainder}")

    cleaned = "\n".join(out_lines)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")

    # When EVERY prose line was brief echo, the section is pure agent instruction
    # and must not ship. Return it empty rather than preserving the text: an
    # empty body is what `proposal_hollow_kb_fill.section_answers_missing`
    # triggers on, and `fill_hollow_sections_for_pipeline` — wired into the main
    # build — then refills the tab from the KB with real, grounded content.
    #
    # Preserving the instructions would ship the exact defect this function
    # exists to remove; emptying hands the tab to the one pass that can actually
    # rewrite it. Only RFP tabs reach here (the caller drafts source="rfp"), and
    # `_skip_section` never skips those, so the refill always applies.
    #
    # Headings are dropped along with the prose deliberately: a lone surviving
    # "## Executive Summary" is NOT matched by section_answers_missing's
    # bare-heading rule (which covers only Qualifications/Experience/References/
    # Team), so it would slip through the refill and ship as an empty heading.
    if _has_prose(text) and not _has_prose(cleaned):
        return ""
    return cleaned


def _has_prose(text: str) -> bool:
    """True when text carries a line that is not a heading, table row or tag."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", ">")):
            continue
        if any(tag in stripped for tag in _BRIEF_ECHO_PROTECTED_TAGS):
            continue
        return True
    return False


def convert_instruction_blocks(text: str) -> str:
    """Turn narrated-instruction paragraphs/blockquotes into visible handoff tags.

    Unlike `convert_bare_confirmation_lines` (single lines starting with a
    trigger phrase), this scans whole blank-line-delimited blocks for
    instruction markers anywhere inside them — the shape the Carson leak
    actually had (a multi-line blockquote of the model's own submission
    rules).

    Routing is three-way per block, and the "keep or drop" decision inside a
    matched block is made per SENTENCE, not per block:
      1. Sentences naming a concrete RFP deliverable (references, forms,
         attachments, certifications, insurance, signatures, addenda,
         licences, bonds) or a person to confirm with -> kept, block becomes
         [MANUAL FILL: Sonja — <surviving sentences>].
      2. Otherwise, sentences about layout/imagery/presentation -> kept,
         block becomes [DESIGNER NOTE: <surviving sentences> — edit if it
         needs changing, or delete this note if the RFP does not require it].
      3. Pure meta-commentary with no deliverable behind it ("Do not invent
         contacts…") has no keywords in either list and is dropped — not
         flagged, just removed, so it never buries a real flag.

    If nothing in a matched block survives rule 3, the whole block is
    removed. A block already containing `[` or `]` is left untouched — it is
    probably already tagged.
    """
    if not text:
        return text

    parts = _INSTRUCTION_BLOCK_SPLIT_RE.split(text)
    out_parts: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            # This is a blank-line separator between blocks — pass through.
            out_parts.append(part)
            continue

        block = part
        lowered_block = block.lower()
        if "[" in block or "]" in block or not _instruction_block_matches(lowered_block):
            out_parts.append(block)
            continue

        stripped_lines = [_strip_instruction_line_prefix(ln) for ln in block.split("\n")]
        collapsed = " ".join(ln for ln in stripped_lines if ln)
        collapsed = re.sub(r"[ \t]{2,}", " ", collapsed).strip()

        category = _classify_instruction_block(collapsed.lower())
        if category is None:
            out_parts.append("")
            continue

        sentences = _INSTRUCTION_SENTENCE_SPLIT_RE.split(collapsed)
        kept = [s.strip() for s in sentences if _instruction_sentence_is_actionable(s.lower())]
        body = " ".join(s for s in kept if s).strip().rstrip(".")
        if not body:
            out_parts.append("")
            continue

        if category == "manual":
            out_parts.append(f"[MANUAL FILL: Sonja — {body}]")
        else:
            body = (
                f"{body} — edit if it needs changing, or delete this note "
                "if the RFP does not require it"
            )
            out_parts.append(f"[DESIGNER NOTE: {body}]")

    result = "".join(out_parts)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


# Bare "Confirm before submit —" / "Needs your input —" / "Action needed —" lines
# that the LLM writes as ordinary prose. In DuPage they shipped as plain visible
# text ("Confirm before submit — Primary case study, workforce board …"). Convert
# to proper MANUAL FILL tags so (a) writers see them as a real gap in the UI's
# flag panel, and (b) the export-time internal-handoff scrub removes them from
# the client-facing DOCX / Google Doc.
_BARE_CONFIRMATION_LINE_RE = re.compile(
    r"(?im)^[ \t]*"
    r"(?:confirm\s+before\s+(?:submit|submission)"
    r"|needs?\s+your\s+input"
    r"|action\s+needed"
    r"|flag\s+for\s+sonja)"
    r"\s*[—–\-,:]\s*"
    r"(?P<body>[^\n]{3,400})"
    r"[ \t]*$"
)


def convert_bare_confirmation_lines(text: str) -> str:
    """Turn free-text 'Confirm before submit — X' lines into [MANUAL FILL: …] tags.

    Only matches lines that START with the trigger phrase followed by a dash,
    comma, or colon and a real body — a paragraph containing the phrase
    mid-sentence is left alone.
    """
    if not text:
        return text

    def _repl(match: re.Match[str]) -> str:
        body = match.group("body").strip().rstrip(".;")
        if not body or "[" in body or "]" in body:
            return match.group(0)
        return f"[MANUAL FILL: Sonja — {body}]"

    return _BARE_CONFIRMATION_LINE_RE.sub(_repl, text)


_NOTE_TO_STAFF_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:>[\s]*)?"
    r"Note\s+to\s+(?:Sonja|leadership|team)\s*[—–\-:,]\s*"
    r"(?P<body>[^\n]+)"
    r"[ \t]*$"
)


def convert_note_to_staff_lines(text: str) -> str:
    """Turn internal 'Note to Sonja: …' blockquote lines into MANUAL FILL tags."""

    def _repl(match: re.Match[str]) -> str:
        body = (match.group("body") or "").strip()
        if not body:
            return ""
        return f"[MANUAL FILL: Sonja — {body}]"

    return _NOTE_TO_STAFF_LINE_RE.sub(_repl, text)


def _is_markdown_table_separator_line(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(not c or _SEP_CELL_RE.match(c) for c in cells)


def _is_reference_table_header_line(line: str) -> bool:
    if not line.strip().startswith("|"):
        return False
    low = line.casefold()
    return (
        "contact" in low
        or "organization" in low
        or "phone" in low
        or "email" in low
    ) and "|" in line


def _is_hollow_reference_table_row(line: str) -> bool:
    if not line.strip().startswith("|"):
        return False
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    non_empty = [c for c in cells if c]
    if not non_empty:
        return True
    if (
        len(cells) >= 4
        and len(non_empty) <= 3
        and re.match(r"^\d+$", non_empty[0])
    ):
        return True
    return False


def _is_all_empty_pipe_row(line: str) -> bool:
    if not line.strip().startswith("|"):
        return False
    return not any(c.strip() for c in line.strip().strip("|").split("|"))


def _repair_hollow_reference_table_row(line: str, width: int, manual: str) -> str:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    while len(cells) < width:
        cells.append("")
    cells = cells[:width]
    for i, cell in enumerate(cells):
        if not cell:
            cells[i] = manual
    return "| " + " | ".join(cells) + " |"


def scrub_broken_reference_pipe_rows(text: str) -> tuple[str, list[str]]:
    """Repair hollow reference-table rows so markdown tables stay column-aligned."""
    if not text or "|" not in text:
        return text, []
    logs: list[str] = []
    manual = "[MANUAL FILL: Sonja — verified contact from ClientList/KB]"
    lines = text.split("\n")
    out: list[str] = []
    repaired = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("|") and _is_reference_table_header_line(stripped):
            width = len(stripped.strip("|").split("|"))
            out.append(line)
            i += 1
            if i < len(lines) and _is_markdown_table_separator_line(lines[i].strip()):
                out.append(lines[i])
                i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                if _is_all_empty_pipe_row(row):
                    i += 1
                    continue
                if _is_hollow_reference_table_row(row):
                    repaired += 1
                    out.append(_repair_hollow_reference_table_row(row, width, manual))
                else:
                    out.append(lines[i])
                i += 1
            continue
        if stripped.startswith("|") and _is_hollow_reference_table_row(stripped):
            width = max(6, len(stripped.strip("|").split("|")))
            repaired += 1
            out.append(_repair_hollow_reference_table_row(stripped, width, manual))
            i += 1
            continue
        out.append(line)
        i += 1
    if repaired:
        logs.append(
            f"Repaired {repaired} hollow reference table row(s) with aligned MANUAL FILL cells"
        )
    text = "\n".join(out)
    text, strip_logs = _strip_orphan_reference_table_headers(text)
    logs.extend(strip_logs)
    return text, logs


def _strip_orphan_reference_table_headers(text: str) -> tuple[str, list[str]]:
    """Fix reference blocks where a table header sits above bullets instead of rows."""
    if not text or "|" not in text:
        return text, []
    lines = text.split("\n")
    out: list[str] = []
    logs: list[str] = []
    rebuilt = 0
    i = 0
    manual = "[MANUAL FILL: Sonja — verified contact from ClientList/KB]"
    while i < len(lines):
        stripped_line = lines[i].strip()
        if stripped_line.startswith("|") and _is_reference_table_header_line(stripped_line):
            header_line = lines[i]
            width = len(stripped_line.strip("|").split("|"))
            j = i + 1
            sep_line = None
            if j < len(lines) and _is_markdown_table_separator_line(lines[j].strip()):
                sep_line = lines[j]
                j += 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].strip().startswith("|"):
                out.append(lines[i])
                i += 1
                continue
            bullet_rows: list[str] = []
            k = j
            while k < len(lines):
                row = lines[k].strip()
                if not row:
                    k += 1
                    continue
                if row.startswith("-") or row.startswith("*") or row.startswith("[MANUAL FILL"):
                    bullet_rows.append(row)
                    k += 1
                    continue
                break
            if bullet_rows:
                out.append(header_line)
                if sep_line:
                    out.append(sep_line)
                for idx, bullet in enumerate(bullet_rows, start=1):
                    org = bullet
                    if bullet.startswith("-") or bullet.startswith("*"):
                        org = re.sub(r"^[-*]\s+", "", bullet)
                        org = org.split("—")[0].split(" - ")[0].strip()
                        org = re.sub(r"\*+", "", org).strip()
                    cells = [manual] * width
                    if width > 0:
                        cells[0] = str(idx)
                    org_col = 3 if width >= 4 else max(0, width - 1)
                    if org_col < width:
                        cells[org_col] = org
                    out.append("| " + " | ".join(cells) + " |")
                    rebuilt += 1
                i = k
                continue
        out.append(lines[i])
        i += 1
    if rebuilt:
        logs.append(
            f"Rebuilt {rebuilt} reference table row(s) from misaligned bullet list"
        )
    return "\n".join(out), logs


# Table cells and inline spans often ship "Confirm before submit — …" as visible
# prose instead of [MANUAL FILL] tags. Match cell interiors bounded by pipes.
_INLINE_TABLE_CONFIRM_RE = re.compile(
    r"(?i)(?<=\|)\s*"
    r"(?:confirm\s+before\s+(?:submit|submission)"
    r"|needs?\s+your\s+input"
    r"|action\s+needed"
    r"|flag\s+for\s+sonja)"
    r"\s*[—–\-,:]\s*"
    r"(?P<body>[^|\n\]]{3,400})"
    r"\s*(?=\|)"
)


def convert_inline_confirmation_phrases(text: str) -> str:
    """Turn inline/table 'Confirm before submit — X' into [MANUAL FILL: …] tags."""
    if not text:
        return text

    def _repl(match: re.Match[str]) -> str:
        body = match.group("body").strip().rstrip(".;")
        if not body or "[" in body or "]" in body:
            return match.group(0)
        return f"[MANUAL FILL: Sonja — {body}]"

    return _INLINE_TABLE_CONFIRM_RE.sub(_repl, text)


# Leaked internal identifiers inside MANUAL FILL descriptions — the DuPage draft
# shipped:
#   [MANUAL FILL: Sonja, deterministic.manuscript_locks.primary_contact_lock_is_ron_comer_but_this_section_names_haley_n\tPrimary contact lock is 'Ron Comer', but this section names Ron Comer, Sonja…]
# The producer concatenated a rule_id (dotted snake_case) with a tab and the
# human message. Strip the rule_id + tab so writers see the plain-English message.
_LEAKED_IDENTIFIER_RE = re.compile(
    r"(\[MANUAL\s+FILL:\s*[^,\]]+),\s*"
    r"[a-z][a-z0-9_.]*(?:_[a-z0-9]+){2,}"        # snake_case with ≥3 underscored tokens
    r"[ \t]*",
    re.IGNORECASE,
)


def strip_leaked_manual_fill_identifiers(text: str) -> str:
    """Drop dotted-snake_case rule_ids that leaked into MANUAL FILL descriptions."""
    if not text:
        return text
    cleaned = _LEAKED_IDENTIFIER_RE.sub(r"\1 — ", text)
    cleaned = cleaned.replace("\t", " ")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


# Standalone subheadings with no body content beneath them — the DuPage draft had
# "Client Voice" heading with nothing under it, "City of Medford: Rogue X
# Community Recreation Center" with only a heading, "1.3 — Business Information"
# with only the label "Business Information" beneath. Reader / designer sees a
# labelled empty box — worse than dropping the heading entirely.
_MD_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+.+$")


def collapse_empty_subheadings(text: str) -> str:
    """Remove heading lines that have no substantive body content beneath them.

    A line counts as "body" only if it contains visible non-tag content before
    the next heading. Standalone MANUAL FILL / DESIGNER NOTE / instruction-tag
    lines are not treated as body — an empty section with only a handoff tag is
    still an empty section for the reader.
    """
    if not text:
        return text
    lines = text.split("\n")
    keep = [True] * len(lines)

    def _heading_level(line: str) -> int:
        match = _MD_HEADING_LINE_RE.match(line)
        if match:
            hashes = re.match(r"^\s*(#{1,6})\s+", line)
            return len(hashes.group(1)) if hashes else 2
        return 6

    def _is_heading(idx: int) -> bool:
        if not (0 <= idx < len(lines)):
            return False
        if _MD_HEADING_LINE_RE.match(lines[idx]):
            return True
        # Bold-only title line (**Team Qualifications Summary**) — not money rows.
        match = re.match(r"^\s*\*\*([^*]+)\*\*\s*$", lines[idx])
        if not match:
            return False
        inner = match.group(1).strip()
        if ":" in inner or "$" in inner:
            return False
        return True

    def _has_body_before_next_heading(start: int) -> bool:
        j = start + 1
        start_level = _heading_level(lines[start])
        while j < len(lines):
            if _is_heading(j):
                # Nested headings (### under ##) do not make the parent empty.
                if _heading_level(lines[j]) <= start_level:
                    return False
                j += 1
                continue
            body = lines[j].strip()
            if not body:
                j += 1
                continue
            # Standalone tag lines don't count as body.
            if (
                INTERNAL_HANDOFF_TAG_RE.fullmatch(body)
                or _INLINE_INSTRUCTION_TAG_RE.fullmatch(body)
            ):
                j += 1
                continue
            return True
        return False

    for i in range(len(lines)):
        if _is_heading(i) and not _has_body_before_next_heading(i):
            keep[i] = False

    cleaned = "\n".join(line for line, k in zip(lines, keep) if k)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip("\n")


def scrub_client_facing_section_artifacts(text: str) -> str:
    """Strip evidence markers + pricing flags from manuscript section bodies.

    This is the AUTHORING-time scrub, applied when a section is generated or
    edited (proposal_budget_content.py, proposal_self_edit_loop.py,
    proposal_section_editor.py) and persisted to the draft. It deliberately
    does NOT touch [MANUAL FILL], [DESIGNER NOTE], [VERIFY], or [FLAG FOR ...]
    — those are legitimate in-progress handoffs (e.g. procurement-form fields
    a human must still complete; see proposal_budget_content.py:112-168) that
    must survive the authoring/editing round trip. Removing PRICING FLAG here
    is safe because it is never a legitimate handoff — it is purely an internal
    pricing-review note.

    For the literal document that leaves the building (DOCX / Google Doc
    export), use scrub_text_for_client_export below, which also strips the
    handoff tags this function preserves.

    Order matters:
      0. convert instruction-shaped paragraphs/blockquotes ("ACTION REQUIRED
         BEFORE SUBMISSION…") INTO real MANUAL FILL / DESIGNER NOTE tags
         before the line-level rule below can re-match a leftover line of one
      1. convert bare "Confirm before submit — X" / template tokens INTO real
         MANUAL FILL tags so writers see them in the flag panel
      2. sanitize any MANUAL FILL tags that already contain leaked internal
         rule_ids (dotted snake_case + tab) so the display copy is human-readable
      3. strip inline [REMOVE:] / [NOTE:] / [TODO:] instruction tags — never
         legitimate authoring artifacts
      4. drop evidence markers and internal pricing flags (existing behavior)
      5. collapse standalone empty subheadings ("Client Voice" with no body)
      6. normalize bold/plain "Designer Note:" prose into [DESIGNER NOTE: …]
         so the manuscript UI renders the callout box
      7. repair flattened markdown tables (one-line pipe dumps) back into
         one-row-per-line tables so Word / Google Doc / in-app stay in sync
    """
    cleaned = text or ""
    cleaned = convert_instruction_blocks(cleaned)
    cleaned = convert_note_to_staff_lines(cleaned)
    cleaned = convert_bare_confirmation_lines(cleaned)
    cleaned = convert_unresolved_template_tokens(cleaned)
    cleaned = strip_leaked_manual_fill_identifiers(cleaned)
    cleaned = strip_inline_instruction_tags(cleaned)
    cleaned = strip_internal_pricing_flags(strip_evidence_citation_markers(cleaned))
    cleaned = collapse_empty_subheadings(cleaned)
    cleaned = normalize_designer_note_markup(cleaned)
    cleaned = strip_schema_description_tables(cleaned)
    cleaned = repair_flattened_markdown_tables(cleaned)
    return cleaned


# Bold / plain "Designer Note:" labels LLMs often emit instead of the canonical tag.
_DESIGNER_NOTE_PROSE_RE = re.compile(
    r"(?im)^[ \t]*(?:\*\*|__)?[ \t]*Designer[ \t]+Note(?:\*\*|__)?[ \t]*:[ \t]*(.+?)\s*$"
)


def normalize_designer_note_markup(text: str) -> str:
    """Turn ``**Designer Note:** …`` / ``Designer Note: …`` into ``[DESIGNER NOTE: …]``.

    The manuscript UI only promotes the bracket tag into a callout div. Bold prose
    labels render as ordinary body text — which is the wrong handoff shape.
    """
    if not text or "designer note" not in text.casefold():
        return text

    def _repl(match: re.Match[str]) -> str:
        body = (match.group(1) or "").strip()
        body = re.sub(r"^(?:\*\*|__)+|(?:\*\*|__)+$", "", body).strip()
        if not body:
            return match.group(0)
        if re.match(r"^\[DESIGNER\s+NOTE\b", body, re.I):
            return body
        # Already wrapped with trailing ] from a half-formed tag.
        if body.endswith("]") and body.upper().startswith("[DESIGNER"):
            return body
        return f"[DESIGNER NOTE: {body}]"

    return _DESIGNER_NOTE_PROSE_RE.sub(_repl, text)


def apply_designer_ready_markup_polish_to_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Normalize designer-note tags + collapse empty H2/H3 shells — no new facts.

    Complete Scan / Generate final polish so the manuscript is handoff-shaped
    for layout (bracket callouts, no empty heading shells).
    """
    logs: list[str] = []
    sections = []
    changed_any = False
    for section in draft.sections:
        body = section.content or ""
        if not body.strip():
            sections.append(section)
            continue
        polished = normalize_designer_note_markup(body)
        polished = collapse_empty_subheadings(polished)
        if polished != body:
            changed_any = True
            title = (section.title or section.id or "section").strip()
            logs.append(f"{title}: designer-ready markup polish")
            sections.append(section.model_copy(update={"content": polished}))
        else:
            sections.append(section)
    if not changed_any:
        return draft, []
    return draft.model_copy(update={"sections": sections}), logs


# --- Internal handoff tags: THE single definition ---------------------------------
#
# This is the one place that defines "what counts as an internal handoff tag".
# proposal_rfp_optional_claim_scrub.strip_handoff_tags_for_scan imports it too —
# do not fork this pattern. (It was forked once: the export copy matched only
# `FLAG\s+FOR`, so the bare `[FLAG: ...]` tags emitted by
# app/services/evidence_trust/flags.py — flag_confirm / flag_claim_mismatch /
# flag_provenance, which reach section.content via
# claim_validator.validate_and_flag_section — shipped verbatim in client-facing
# DOCX exports.)
#
# These tags are legitimate while a proposal is being authored (see
# scrub_client_facing_section_artifacts above), but none of them may ever appear
# in the file actually submitted/sent to a client or RFP reviewer. Observed
# defects: DOCX export rendered [DESIGNER NOTE: ...] as a styled "DESIGNER NOTE"
# block, and [PRICING FLAG: ...] / [FLAG: ...] reached export verbatim.
#
# `\b` after each alternative keeps ordinary prose safe — "[FLAGSHIP PROGRAM]"
# and "[VERIFICATION SUMMARY]" are not tags and must survive the scrub.
INTERNAL_HANDOFF_TAG_RE = re.compile(
    r"\[(?:MANUAL\s+FILL|DESIGNER\s+NOTE|VERIFY|PRICING\s+FLAG|FLAG)\b[^\]]*\]",
    re.IGNORECASE,
)


def strip_internal_handoff_tags(text: str) -> str:
    """Remove [MANUAL FILL]/[DESIGNER NOTE]/[VERIFY]/[FLAG ...]/[PRICING FLAG] blocks.

    Export-only: these tags must survive authoring (see
    scrub_client_facing_section_artifacts) but must never reach a document
    that leaves the agency.
    """
    if not text:
        return text
    cleaned = INTERNAL_HANDOFF_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def find_instruction_leaks(text: str) -> list[str]:
    """Export tripwire: report blocks of narrated-instruction prose that would
    otherwise ship untagged into a client-facing export.

    `convert_instruction_blocks` (above) is pattern matching and cannot
    anticipate every phrasing the model might invent. This is the safety net:
    it reuses the exact same marker list, so it never disagrees with the
    converter about what counts as a leak, but it does NOT rewrite or block
    anything. It only reports. Per product decision, an instruction leak must
    WARN LOUDLY at export time, never hard-fail — a false positive here must
    never strand a submission against a deadline.

    Text already inside an internal handoff tag ([MANUAL FILL …],
    [DESIGNER NOTE …], [VERIFY …], [FLAG …]) is ignored: those tags are
    stripped at export by strip_internal_handoff_tags, so a marker phrase
    quoted inside one (e.g. a MANUAL FILL body that repeats "cannot be
    submitted") is not a leak — it never reaches the client.

    Returns the offending excerpts (the matched block, collapsed to a single
    line), empty when the text is clean.
    """
    if not text:
        return []

    # Blank out already-tagged spans first so a marker phrase quoted inside a
    # legitimate tag body isn't reported as a leak.
    masked = INTERNAL_HANDOFF_TAG_RE.sub(lambda m: " " * len(m.group(0)), text)

    leaks: list[str] = []
    for part in _INSTRUCTION_BLOCK_SPLIT_RE.split(masked):
        if not part.strip():
            continue
        lowered = part.lower()
        if not _instruction_block_matches(lowered):
            continue
        stripped_lines = [_strip_instruction_line_prefix(ln) for ln in part.split("\n")]
        collapsed = " ".join(ln for ln in stripped_lines if ln)
        collapsed = re.sub(r"[ \t]{2,}", " ", collapsed).strip()
        if collapsed:
            leaks.append(collapsed)
    return leaks


def scrub_text_for_client_export(text: str) -> str:
    """Full export-time scrub: everything scrub_client_facing_section_artifacts
    strips from saved draft content, plus the internal handoff tags that are
    allowed to persist through authoring but must never reach an exported
    document (DOCX, Google Doc, plain text)."""
    return strip_internal_handoff_tags(scrub_client_facing_section_artifacts(text or ""))


def plain_text_for_export(markdown: str) -> str:
    """Strip markdown markers for plain Google Docs text (keep list markers)."""
    text = markdown or ""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = scrub_text_for_client_export(text)
    return text.strip()


def _strip_inline_md(text: str) -> str:
    """Remove bold/italic/code markers but keep the words."""
    t = text or ""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = scrub_text_for_client_export(t)
    return t.strip()


def _is_table_row(line: str) -> bool:
    trimmed = line.strip()
    if "|" not in trimmed:
        return False
    if re.match(
        r"^\[(?:MANUAL\s+FILL|VERIFY|FLAG|PRICING\s+FLAG|DESIGNER\s+NOTE)\b",
        trimmed,
        re.I,
    ):
        return False
    cells = [c for c in trimmed.strip("|").split("|")]
    return len(cells) >= 2


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line.strip()))


def _parse_table_row(line: str) -> list[str]:
    return [_strip_inline_md(cell) for cell in line.strip().strip("|").split("|")]


# Flattened-table repair: LLM / cert-scrub / save paths sometimes collapse an
# entire markdown table into one line (newlines between rows become spaces).
_ROW_BOUNDARY_RE = re.compile(r"\|[ \t]+\|(?=[ \t]*\S)")
_SEP_FRAGMENT_RE = re.compile(r"\|[ \t]*:?-{2,}:?[ \t]*\|")
_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _is_flattened_table_line(line: str) -> bool:
    """True when one line holds multiple markdown table rows."""
    if not line.startswith("|"):
        return False
    pipes = line.count("|")
    if pipes <= 6:
        return False
    boundaries = len(_ROW_BOUNDARY_RE.findall(line))
    if boundaries < 1:
        return False
    if _SEP_FRAGMENT_RE.search(line):
        return True
    return pipes >= 16 and boundaries >= 2


def _split_flattened_table_line(line: str) -> list[str]:
    """Turn one flattened pipe-dump into one markdown table row per line."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    i = 0
    n = len(cells)
    header: list[str] = []
    while i < n and cells[i] and not _SEP_CELL_RE.match(cells[i]):
        header.append(cells[i])
        i += 1
    width = max(len(header), 1)
    while i < n and not cells[i]:
        i += 1
    while i < n and _SEP_CELL_RE.match(cells[i] or ""):
        i += 1
    while i < n and not cells[i]:
        i += 1

    rows: list[list[str]] = []
    current: list[str] = []
    while i < n:
        cell = cells[i]
        i += 1
        if not cell:
            if not current:
                continue
            if len(current) >= width:
                rows.append(current[:width])
                current = []
                continue
            current.append("")
            if len(current) >= width:
                rows.append(current)
                current = []
            continue
        current.append(cell)
        if len(current) >= width:
            rows.append(current)
            current = []
    if current:
        rows.append((current + [""] * width)[:width])

    sep = "| " + " | ".join("---" for _ in range(width)) + " |"
    out = ["| " + " | ".join(header) + " |", sep]
    for row in rows:
        padded = (row + [""] * width)[:width]
        out.append("| " + " | ".join(padded) + " |")
    return out


# A table that describes its own schema instead of carrying data.
#
# Observed on the Gilroy References tab, which shipped:
#
#   | FIELD          | WHAT WE PROVIDE                                  |
#   | Organization   | Client name and sector                           |
#   | Contact        | Name and title of the person who directed ...    |
#   | Phone & Email  | Direct contact information, not routed through us|
#
# That is the writer explaining what a reference entry WOULD contain rather
# than naming three references. It reads as content to a reviewer and is
# useless to a designer, and it is invisible to strip_brief_echo_sentences
# because that function deliberately never touches table rows.
#
# Detection is by HEADER only, which keeps it precise: a real reference table's
# headers are the data's own field names ("Organization | Contact | Phone"),
# never a generic left-hand "Field" paired with a right-hand "what we provide".
_SCHEMA_TABLE_LEFT_HEADERS = frozenset(
    {
        "field",
        "fields",
        "item",
        "items",
        "element",
        "elements",
        "category",
        "component",
        "attribute",
        "data point",
        "information",
    }
)
_SCHEMA_TABLE_RIGHT_HINTS = (
    "what we provide",
    "what we will provide",
    "what we include",
    "what we supply",
    "what you get",
    "what we submit",
    "description",
    "details provided",
    "what this includes",
    "contents",
)


def _is_table_separator_row(line: str) -> bool:
    """`| --- | :--- |` — only pipes, dashes, colons and whitespace."""
    stripped = (line or "").strip()
    if not stripped.startswith("|") or "-" not in stripped:
        return False
    return set(stripped) <= set("|-: \t")


def _is_schema_table_header(line: str) -> bool:
    cells = [c.strip().casefold() for c in line.strip().strip("|").split("|")]
    if len(cells) < 2:
        return False
    if cells[0] not in _SCHEMA_TABLE_LEFT_HEADERS:
        return False
    rest = " ".join(cells[1:])
    return any(hint in rest for hint in _SCHEMA_TABLE_RIGHT_HINTS)


def strip_schema_description_tables(text: str) -> str:
    """Drop tables that describe what a section would contain instead of containing it.

    Surrounding prose and handoff tags are untouched — the section keeps its real
    narrative and its [MANUAL FILL], losing only the fake data block.
    """
    body = text or ""
    if "|" not in body:
        return body

    lines = body.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    removed = False
    while i < n:
        line = lines[i]
        if (
            line.strip().startswith("|")
            and i + 1 < n
            and _is_table_separator_row(lines[i + 1])
            and _is_schema_table_header(line)
        ):
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                i += 1
            removed = True
            # Drop a heading that introduced only this table ("### Reference Format").
            while out and not out[-1].strip():
                out.pop()
            if out and out[-1].lstrip().startswith("#"):
                out.pop()
            continue
        out.append(line)
        i += 1

    if not removed:
        return body
    cleaned = "\n".join(out)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip() + "\n"


def repair_flattened_markdown_tables(text: str) -> str:
    """Re-insert newlines so each markdown table row is its own line.

    Authoring-time + persist-time: the stored manuscript must stay as real
    markdown tables. Export parsers also call this so older flattened drafts
    still render, but the source of truth is the markdown itself.
    """
    if not text or "|" not in text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not _is_flattened_table_line(stripped):
            out_lines.append(line)
            continue
        out_lines.extend(_split_flattened_table_line(stripped))
    return _unwrap_handoff_table_lines(_merge_split_amount_rows("\n".join(out_lines)))


def _unwrap_handoff_table_lines(text: str) -> str:
    """Turn leftover handoff-tag pipe debris into prose — never real table rows.

    A flatten pass can leave ``[MANUAL FILL: … | leftover]`` as a fake table.
    Rows that start with ``|`` and still have real cells (Yes, amounts, names)
    must stay markdown tables even when a cell is ``[VERIFY: …]``.
    """
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            real_cells = [c for c in cells if c and not _is_handoff_only_cell(c)]
            if real_cells:
                out.append(line)
                continue
        if (
            "|" in stripped
            and re.match(
                r"^\[(?:MANUAL\s+FILL|VERIFY|FLAG|PRICING\s+FLAG|DESIGNER\s+NOTE)\b",
                stripped.lstrip("| ").strip(),
                re.I,
            )
        ):
            inner = stripped.strip("|").replace("|", " — ").strip()
            inner = re.sub(r"[ \t]{2,}", " ", inner)
            out.append(inner)
            continue
        out.append(line)
    return "\n".join(out)


def _is_handoff_only_cell(cell: str) -> bool:
    t = (cell or "").strip()
    return bool(
        re.fullmatch(
            r"\[(?:MANUAL\s+FILL|VERIFY|FLAG|PRICING\s+FLAG|DESIGNER\s+NOTE)\b[^\]]*\]",
            t,
            re.I,
        )
    )


def _merge_split_amount_rows(text: str) -> str:
    """Join a 1-cell label row with the following 1-cell money row.

    A previous flatten/split pass can turn ``| **Total** | | **$199** |``
    into two lines. Restore the empty middle cell.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if "|" in line else []
        nxt_cells = [c.strip() for c in nxt.strip().strip("|").split("|")] if "|" in nxt else []
        cells = [c for c in cells if c]
        nxt_cells = [c for c in nxt_cells if c]
        if (
            len(cells) == 1
            and len(nxt_cells) == 1
            and "$" in nxt_cells[0]
            and "$" not in cells[0]
        ):
            out.append(f"| {cells[0]} | | {nxt_cells[0]} |")
            i += 2
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def parse_markdown_parts(markdown: str) -> list[dict[str, Any]]:
    """Split markdown into heading / paragraph / list / table parts for Docs export."""
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = repair_flattened_markdown_tables(text)
    lines = text.split("\n")
    parts: list[dict[str, Any]] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        trimmed = line.strip()

        if not trimmed:
            i += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", trimmed)
        if heading:
            level = min(len(heading.group(1)), 3)
            parts.append(
                {
                    "type": "heading",
                    "level": level,
                    "text": _strip_inline_md(heading.group(2)),
                }
            )
            i += 1
            continue

        if _is_table_row(trimmed):
            table_lines: list[str] = []
            while i < len(lines):
                cur = lines[i].strip()
                if not cur:
                    # Blank lines inside a markdown table are common after LLM
                    # rewrites. Peek ahead — stay in the table if the next
                    # non-empty line is still a pipe row.
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and _is_table_row(lines[j].strip()):
                        i = j
                        continue
                    break
                if not _is_table_row(cur):
                    break
                table_lines.append(cur)
                i += 1
            data_lines = [row for row in table_lines if not _is_table_separator(row)]
            if data_lines:
                headers = _parse_table_row(data_lines[0])
                rows = [_parse_table_row(row) for row in data_lines[1:]]
                # Drop leftover separator cells if the sep row was misclassified.
                if rows and all(
                    re.fullmatch(r":?-{2,}:?", (c or "").strip()) for c in rows[0]
                ):
                    rows = rows[1:]
                hdr_count = max(len(headers), 1)
                # Clamp to header width — rows with extra cells (from
                # literal "|" inside cell text) must not inflate the grid.
                headers = (headers + [""] * hdr_count)[:hdr_count]
                clamped_rows: list[list[str]] = []
                for row in rows:
                    if len(row) > hdr_count:
                        # Merge overflow cells back into the last real column.
                        kept = row[: hdr_count - 1]
                        kept.append(" | ".join(row[hdr_count - 1 :]))
                        clamped_rows.append(kept)
                    else:
                        clamped_rows.append(
                            (row + [""] * hdr_count)[:hdr_count]
                        )
                parts.append({"type": "table", "headers": headers, "rows": clamped_rows})
            continue

        if re.match(r"^[-*]\s+", trimmed) or re.match(r"^\d+\.\s+", trimmed):
            ordered = bool(re.match(r"^\d+\.\s+", trimmed))
            items: list[str] = []
            while i < len(lines):
                cur = lines[i].strip()
                if not cur:
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    nxt = lines[j].strip() if j < len(lines) else ""
                    if nxt and (
                        (ordered and re.match(r"^\d+\.\s+", nxt))
                        or (not ordered and re.match(r"^[-*]\s+", nxt))
                    ):
                        i = j
                        continue
                    break
                if ordered:
                    m = re.match(r"^\d+\.\s+(.+)$", cur)
                    if not m:
                        break
                    items.append(_strip_inline_md(m.group(1)))
                elif re.match(r"^[-*]\s+", cur):
                    items.append(_strip_inline_md(re.sub(r"^[-*]\s+", "", cur)))
                else:
                    break
                i += 1
            if items:
                parts.append({"type": "list", "ordered": ordered, "items": items})
            continue

        para_lines: list[str] = []
        while i < len(lines):
            cur = lines[i]
            cur_trim = cur.strip()
            if not cur_trim:
                break
            if (
                re.match(r"^#{1,4}\s+", cur_trim)
                or _is_table_row(cur_trim)
                or re.match(r"^[-*]\s+", cur_trim)
                or re.match(r"^\d+\.\s+", cur_trim)
            ):
                break
            para_lines.append(cur_trim)
            i += 1
        text = _strip_inline_md(" ".join(para_lines))
        if text:
            # NOTE: _strip_inline_md now runs the export scrub, which removes
            # [DESIGNER NOTE: ...] tags outright (they must never reach an
            # exported document — see proposal_manuscript.strip_internal_
            # handoff_tags). This regex can therefore no longer match; kept
            # only so a "designer_note" part type is never silently produced
            # again if the scrub order changes.
            designer = re.match(
                r"^\[(?:DESIGNER NOTE|Designer Note)\s*:?\s*(.*)\]\s*$",
                text,
                re.I | re.S,
            )
            if designer:
                parts.append(
                    {
                        "type": "designer_note",
                        "text": designer.group(1).strip(),
                    }
                )
            else:
                parts.append({"type": "paragraph", "text": text})

    return parts


def build_manuscript_blocks(
    sections: list["ProposalSection"],
) -> list[tuple[str, str]]:
    """Legacy plain blocks (title, body text). Prefer build_manuscript_structured."""
    blocks: list[tuple[str, str]] = []
    for section in manuscript_sections_for_export(sections):
        title = (section.title or "Untitled section").strip()
        body = plain_text_for_export(section.content or "")
        blocks.append((title, body))
    return blocks


def build_manuscript_structured(
    sections: list["ProposalSection"],
) -> list[dict[str, Any]]:
    """Section title + ordered text/table parts for Google Doc export."""
    out: list[dict[str, Any]] = []
    for section in manuscript_sections_for_export(sections):
        title = (section.title or "Untitled section").strip()
        parts = parse_markdown_parts(section.content or "")
        out.append({"title": title, "parts": parts})
    return out


def build_manuscript_plain_text(sections: list["ProposalSection"]) -> str:
    parts: list[str] = []
    for title, body in build_manuscript_blocks(sections):
        parts.append(f"{title}\n\n{body}")
    return "\n\n—\n\n".join(parts)


def _format_table_plain(headers: list[str], rows: list[list[str]]) -> str:
    cols = max(len(headers), 1)
    hdr = (headers + [""] * cols)[:cols]
    lines = [" | ".join(hdr)]
    for row in rows:
        padded = (list(row) + [""] * cols)[:cols]
        lines.append(" | ".join(padded))
    return "\n".join(lines)


def build_google_doc_bulk_export(
    doc_title: str,
    sections: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, int, int, bool]]]:
    """
    Single string for insertText plus style spans: (start, end, heading_level, bold).
    Indices are 0-based in the returned text; map to Docs with insertIndex + start.
    Tables are plain pipe-separated lines to avoid expensive table API calls.
    """
    pieces: list[str] = []
    spans: list[tuple[int, int, int, bool, bool]] = []
    pos = 0

    def add_line(line: str, *, heading: int | None = None, bold: bool = False) -> None:
        nonlocal pos
        block = line + "\n"
        start = pos
        pieces.append(block)
        pos += len(block)
        if heading and line.strip():
            spans.append((start, pos, heading, bold, False))
        elif bold and line.strip():
            spans.append((start, pos, 0, True, False))

    add_line(doc_title.strip(), heading=1, bold=True)
    add_line("")

    for section in sections:
        title = (section.get("title") or "Untitled").strip()
        add_line(title, heading=1)

        for part in section.get("parts") or []:
            ptype = part.get("type")
            if ptype == "heading":
                text = (part.get("text") or "").strip()
                if text:
                    # Plain line — styling every subheading exceeds Docs write quota.
                    add_line(text)
                continue
            if ptype == "table":
                headers = part.get("headers") or []
                rows = part.get("rows") or []
                if headers:
                    tbl = _format_table_plain(headers, rows)
                    for tbl_line in tbl.split("\n"):
                        add_line(tbl_line)
                    add_line("")
                continue
            if ptype == "list":
                items = part.get("items") or []
                ordered = bool(part.get("ordered"))
                for i, item in enumerate(items):
                    prefix = f"{i + 1}. " if ordered else "• "
                    add_line(f"{prefix}{(item or '').strip()}")
                continue
            text = (part.get("text") or "").strip()
            if text:
                for chunk in re.split(r"\n{2,}", text):
                    chunk = chunk.strip()
                    if chunk:
                        add_line(chunk)

        add_line("")

    return "".join(pieces), spans


def build_google_doc_export_blocks(
    doc_title: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Ordered blocks for Docs export: {"kind": "text", "text", "spans"} or
    {"kind": "table", "headers", "rows"}. Spans are 0-based within each text block.
    """
    blocks: list[dict[str, Any]] = []
    pieces: list[str] = []
    spans: list[tuple[int, int, int, bool, bool]] = []
    pos = 0

    def flush_text() -> None:
        nonlocal pieces, spans, pos
        if not pieces:
            return
        blocks.append(
            {
                "kind": "text",
                "text": "".join(pieces),
                "spans": list(spans),
            }
        )
        pieces = []
        spans = []
        pos = 0

    def add_line(
        line: str,
        *,
        heading: int | None = None,
        bold: bool = False,
        italic: bool = False,
    ) -> None:
        nonlocal pos
        block = line + "\n"
        start = pos
        pieces.append(block)
        pos += len(block)
        if heading and line.strip():
            spans.append((start, pos, heading, bold, italic))
        elif bold or italic:
            if line.strip():
                spans.append((start, pos, 0, bold, italic))

    add_line(doc_title.strip(), heading=1, bold=True)
    add_line("")

    for section in sections:
        title = (section.get("title") or "Untitled").strip()
        add_line(title, heading=2)

        for part in section.get("parts") or []:
            ptype = part.get("type")
            if ptype == "heading":
                text = (part.get("text") or "").strip()
                if text:
                    level = int(part.get("level") or 2)
                    mapped = min(max(level + 1, 2), 3)
                    add_line(text, heading=mapped)
                continue
            if ptype == "designer_note":
                note = (part.get("text") or "").strip()
                if note:
                    add_line(f"Designer note: {note}", italic=True)
                    add_line("")
                continue
            if ptype == "table":
                headers = part.get("headers") or []
                rows = part.get("rows") or []
                if headers:
                    tbl = _format_table_plain(headers, rows)
                    for tbl_line in tbl.split("\n"):
                        add_line(tbl_line)
                    add_line("")
                continue
            if ptype == "list":
                items = part.get("items") or []
                ordered = bool(part.get("ordered"))
                for i, item in enumerate(items):
                    prefix = f"{i + 1}. " if ordered else "• "
                    add_line(f"{prefix}{(item or '').strip()}")
                continue
            text = (part.get("text") or "").strip()
            if text:
                for chunk in re.split(r"\n{2,}", text):
                    chunk = chunk.strip()
                    if chunk:
                        add_line(chunk)

        add_line("")

    flush_text()
    return blocks
