"""Anti-RFP-echo: writers answer the ask; they never paraphrase the RFP as content.

RFP requirements, Opportunity Understanding, and section briefs are private
coverage/direction inputs. The manuscript must contain only the proposal
answer: what zö will do, how, with which proof, and what the evaluator can
score — never a restatement of what the buyer already wrote.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

ANTI_RFP_ECHO_RULES = """## ANTI-RFP-ECHO (mandatory — every section)

The RFP text, requirement lists, Opportunity Understanding, and section briefs
are INPUTS for coverage — they are NOT source prose for the manuscript.

WRITE ONLY THE PROPOSAL ANSWER:
- What zö will do, deliver, staff, measure, and prove for THIS ask
- Concrete methods, phases, ideas, and commitments in first person (we/our)
- Verified case-study / bio proof tied to the ask (short, specific)
- Tables, bullets, and [VERIFY] / [MANUAL FILL] only for discrete missing facts

NEVER restate, paraphrase, quote, summarize, or "acknowledge" the RFP as content:
- Do not open by telling the client what they asked for, what they already built,
  what they need, or what the RFP requires
- Do not list scope items in the buyer's order as if that were the proposal
- Do not write "you are not asking…", "you built…", "what you need now…",
  "this RFP requires…", "the solicitation asks…", or "we understand you want…"
- Do not narrate evaluation criteria back to the evaluator
- Requirements / criteria blocks in this prompt are a private checklist: answer
  each one with proposal substance, never print the checklist wording

If evidence is thin: write the strongest grounded answer you can + narrow
[VERIFY] / [MANUAL FILL] tags. Do NOT fill the gap by echoing the RFP.
"""

# Token overlap: a sentence that is mostly made of requirement words is echo.
_ECHO_MIN_TOKENS = 6
_ECHO_CONTAINMENT = 0.55
_ECHO_PROTECTED_TAGS = ("[VERIFY", "[MANUAL FILL", "[DESIGNER NOTE", "[PRICING")

# Soft markers that often front RFP paraphrase even when token overlap is moderate.
_ECHO_OPENER_MARKERS = (
    "is not asking",
    "are not asking",
    "not asking us",
    "you built",
    "you already",
    "what you need",
    "what you are looking",
    "this rfp",
    "the rfp requires",
    "the rfp asks",
    "the solicitation",
    "as stated in the rfp",
    "per the rfp",
    "as outlined in the rfp",
    "we understand you",
    "we recognize you",
    "you are seeking",
    "you seek a partner",
    "the client is asking",
    "the buyer requires",
    "evaluation criteria",
    "will be evaluated on",
    "this proposal will be assessed",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for raw in (text or "").casefold().split():
        word = raw.strip(".,;:!?()[]{}\"'`—–-")
        if len(word) > 3:
            out.add(word)
    return out


def _requirement_texts(requirements: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for item in requirements or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(
                item.get("text")
                or item.get("requirement")
                or item.get("description")
                or item.get("title")
                or ""
            ).strip()
        else:
            text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def format_requirements_coverage_block(requirements: Iterable[Any]) -> str:
    """Label requirements as an unquotable coverage checklist for writer prompts."""
    items = _requirement_texts(requirements)
    if not items:
        return ""
    lines = [
        "COVERAGE CHECKLIST (PRIVATE — answer each with proposal substance; "
        "NEVER quote, paraphrase, or list these lines back into the section body):",
    ]
    for idx, item in enumerate(items[:24], start=1):
        lines.append(f"{idx}. {item}")
    return "\n".join(lines)


def _is_rfp_requirement_echo(sentence: str, requirement_token_sets: list[set[str]]) -> bool:
    lowered = sentence.casefold().strip()
    if not lowered:
        return False
    if any(tag.casefold() in lowered for tag in _ECHO_PROTECTED_TAGS):
        return False
    tokens = _tokens(sentence)
    if len(tokens) < _ECHO_MIN_TOKENS:
        return False

    opener = any(marker in lowered for marker in _ECHO_OPENER_MARKERS)
    for req_tokens in requirement_token_sets:
        if not req_tokens:
            continue
        containment = len(tokens & req_tokens) / len(tokens)
        if containment >= _ECHO_CONTAINMENT:
            return True
        # Opener + material overlap with the buyer's ask → echo even if diluted.
        if opener and containment >= 0.35:
            return True
    # Opener alone on a sentence that characterizes the client's ask.
    if opener and len(tokens) >= _ECHO_MIN_TOKENS:
        return True
    return False


def strip_rfp_requirement_echo_sentences(
    text: str,
    requirements: Iterable[Any],
    *,
    extra_directives: Iterable[str] | None = None,
) -> str:
    """Drop sentences that paraphrase RFP requirements / opportunity understanding.

    Headings, tables, and handoff tags are never touched. When every prose line
    was echo, return empty so hollow-fill / repair can rewrite a real answer.
    """
    body = text or ""
    directives = _requirement_texts(requirements)
    if extra_directives:
        directives.extend(d for d in extra_directives if (d or "").strip())
    requirement_token_sets = [_tokens(d) for d in directives]
    if not body.strip() or not requirement_token_sets:
        return body

    out_lines: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith("|")
            or stripped.startswith(">")
            or any(tag in stripped for tag in _ECHO_PROTECTED_TAGS)
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

        sentences = _SENTENCE_SPLIT_RE.split(content)
        kept = [
            s for s in sentences if not _is_rfp_requirement_echo(s, requirement_token_sets)
        ]
        if len(kept) == len(sentences):
            out_lines.append(line)
            continue
        remainder = " ".join(s.strip() for s in kept if s.strip()).strip()
        if not remainder:
            continue
        if prefix:
            out_lines.append(f"{prefix}{remainder}")
        else:
            indent = line[: len(line) - len(line.lstrip())]
            out_lines.append(f"{indent}{remainder}")

    cleaned = "\n".join(out_lines)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()


def opportunity_understanding_directives(understanding: Any) -> list[str]:
    """Flatten Opportunity Understanding dict/list into scrub directives."""
    if not understanding:
        return []
    if isinstance(understanding, str):
        return [understanding] if understanding.strip() else []
    if isinstance(understanding, list):
        return _requirement_texts(understanding)
    if isinstance(understanding, dict):
        out: list[str] = []
        for key, value in understanding.items():
            if isinstance(value, (list, tuple)):
                out.extend(_requirement_texts(value))
            elif isinstance(value, dict):
                out.extend(opportunity_understanding_directives(value))
            else:
                text = str(value or "").strip()
                if text:
                    out.append(f"{key}: {text}" if key else text)
        return out
    return [str(understanding)]
