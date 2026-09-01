"""Resolve open VERIFY/MANUAL FILL tags from facts already stated elsewhere
in the SAME manuscript — generic, for every RFP, not a one-off patch.

Observed on a live RFP: "1.4 — Certifications" states plainly, in confident
prose, that WBENC and WOSB are "current through April 30, 2027." A different
section ("SECTION I, Additional Qualifications") asks the SAME question again
as an open tag — "[VERIFY: current certification expiration dates — Sonja to
confirm]" — even though the document already answers it three sections
earlier. proposal_kb_fact_checker.py resolves tags against the EXTERNAL
knowledge base (Supermemory); nothing checks the document against itself
first, so an already-answered question re-asks itself as an unresolved flag
forever, on every RFP where two sections happen to touch the same fact.

This module closes that gap with the same shape used for the other
completeness backstops in this codebase: one focused LLM pass finds
candidate cross-references, and a DETERMINISTIC gate — the claimed source
sentence must literally appear in the manuscript — decides what is actually
safe to apply. The LLM proposes; the string match disposes. A claim that
fails the literal-text check is dropped, never applied, no matter how
plausible it reads — that is the same zero-fabrication discipline every
other fact-filling step in this pipeline already follows.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_manual_flags import MANUAL_FILL_TAG_RE, VERIFY_TAG_RE

logger = logging.getLogger(__name__)

# Same tag length floor used elsewhere in this pipeline for "is this a real,
# specific ask" — filters out bare `[VERIFY]` markers with no question text.
_MIN_TAG_CHARS = 8

_RESOLVE_SYSTEM = """You check ONE proposal manuscript against itself.

You are given a list of open tags — [VERIFY: ...] or [MANUAL FILL: ...] — each
one an unanswered question the writer left in a section. You are also given
the manuscript's OTHER sections (not the tag's own section).

Your only job: for each tag, decide whether the manuscript's OTHER sections
ALREADY answer that exact question in confident, non-flagged prose (not
another tag, not a hedge, not "we believe" — a stated fact).

Rules:
- Only report a match when the OTHER text states the SAME fact the tag asks
  for — a related topic is not a match. "Certification expiration date" only
  matches text that states an actual expiration date, not text that merely
  mentions certifications.
- source_quote must be copied VERBATIM from the manuscript you were given —
  exact wording, no paraphrase, no summarizing. It will be checked
  character-for-character against the source text; a paraphrase fails that
  check and wastes the answer.
- replacement_text is what should replace the tag in its own section — plain
  prose stating the fact, consistent with how the rest of that section reads.
  Do not include the tag brackets or any [VERIFY]/[MANUAL FILL] wording in it.
- If you are not certain, or no other section states this specific fact, do
  not report it. A missed match costs nothing further down; a wrong one
  writes an incorrect fact into a real bid.

Return JSON only:
{
  "resolved": [
    {
      "tag_id": "the tag_id given to you",
      "source_quote": "verbatim sentence(s) from the OTHER sections proving this",
      "replacement_text": "the fact, stated plainly, to replace the tag with"
    }
  ]
}
Empty "resolved" is a complete, valid, and common answer — do not pad it.
"""


def _extract_open_tags(content: str) -> list[tuple[str, str]]:
    """(full_tag_text, inner_text) for every VERIFY / MANUAL FILL tag."""
    tags: list[tuple[str, str]] = []
    for m in VERIFY_TAG_RE.finditer(content or ""):
        inner = (m.group(1) or "").strip()
        if len(inner) >= _MIN_TAG_CHARS:
            tags.append((m.group(0), inner))
    for m in MANUAL_FILL_TAG_RE.finditer(content or ""):
        full = m.group(0)
        inner = full[1:-1].strip()
        if len(inner) >= _MIN_TAG_CHARS:
            tags.append((full, inner))
    return tags


def _normalize_for_match(text: str) -> str:
    """Collapse whitespace/quote-style differences before the literal check.

    The LLM copies text faithfully but PDFs and markdown rendering vary
    smart-quotes and line-wrapping; this must not be strict enough to reject
    a genuinely verbatim quote over cosmetic differences, and must not be
    loose enough to accept a paraphrase.
    """
    t = (text or "").replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().casefold()


def _quote_appears_in_manuscript(quote: str, manuscript_text: str) -> bool:
    quote_norm = _normalize_for_match(quote)
    if len(quote_norm) < 12:
        return False
    return quote_norm in _normalize_for_match(manuscript_text)


async def resolve_tags_from_manuscript(
    draft: ProposalDraft,
    *,
    max_tags: int = 40,
    only_section_ids: set[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Resolve open tags using facts already stated elsewhere in this SAME
    manuscript. Generic — no per-RFP wording, works on whatever the draft
    actually contains.

    ``only_section_ids``, when given, checks tags belonging to ONLY those
    sections — for a chat-driven "just this section" ask that should not
    require the full Complete & Clean pass. The rest of the manuscript is
    still sent as the SOURCE evidence either way; only which sections'
    OWN tags get checked is scoped down. A ten-section proposal with one
    section flagged costs one small prompt, not eighteen pipeline steps.

    Runs before the external KB search: a fact three sections away is a free,
    zero-latency answer compared to a knowledge-base query, and is the more
    authoritative source when it exists — it is what THIS proposal itself
    already told the evaluator.
    """
    from app.services.proposal_intelligence.agent_base import safe_chat_json

    sections = list(draft.sections)
    tagged: list[tuple[int, str, str, str]] = []  # (idx, tag_id, full_tag, inner_text)
    for idx, section in enumerate(sections):
        if only_section_ids is not None and section.id not in only_section_ids:
            continue
        for full_tag, inner in _extract_open_tags(section.content or ""):
            tag_id = f"t{len(tagged)}"
            tagged.append((idx, tag_id, full_tag, inner))
            if len(tagged) >= max_tags:
                break
        if len(tagged) >= max_tags:
            break

    if not tagged:
        return draft, []

    tags_block = "\n".join(
        f'- tag_id={tag_id} | section="{sections[idx].title}" | asks: {inner[:220]}'
        for idx, tag_id, _full, inner in tagged
    )
    manuscript_block = "\n\n---\n\n".join(
        f'### {s.title}\n{(s.content or "").strip()}'
        for s in sections
        if (s.content or "").strip()
    )

    # This runs twice per Scan-RFP pass (plus more elsewhere) against a
    # manuscript that's mostly unchanged between calls — sections before the
    # first one edited in between still share a byte-identical prefix, so this
    # is worth caching even though it isn't guaranteed byte-identical overall.
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _RESOLVE_SYSTEM},
            {
                "role": "user",
                "content": f"Open tags to check:\n{tags_block}",
            },
        ],
        max_tokens=3072,
        agent_name="cross_reference_resolver",
        cache_prefix=(
            f"Full manuscript (a tag's own section may itself be one of "
            f"these — only a DIFFERENT section counts as a source):\n"
            f"{manuscript_block[:90000]}\n\n"
        ),
    )
    proposed = raw.get("resolved") if isinstance(raw, dict) else None
    if not isinstance(proposed, list):
        return draft, []

    by_tag_id = {tag_id: (idx, full_tag) for idx, tag_id, full_tag, _inner in tagged}
    full_manuscript_text = "\n\n".join((s.content or "") for s in sections)

    applied: list[str] = []
    new_sections = list(sections)
    for item in proposed:
        if not isinstance(item, dict):
            continue
        tag_id = str(item.get("tag_id") or "")
        quote = str(item.get("source_quote") or "")
        replacement = str(item.get("replacement_text") or "").strip()
        if not tag_id or tag_id not in by_tag_id or not quote or not replacement:
            continue
        idx, full_tag = by_tag_id[tag_id]
        # The hard gate: the claimed proof must literally exist in the
        # manuscript. Fails silently and safely otherwise — the tag stays.
        if not _quote_appears_in_manuscript(quote, full_manuscript_text):
            logger.info(
                "cross_reference_resolver: rejected unverifiable quote for %s",
                tag_id,
            )
            continue
        section = new_sections[idx]
        content = section.content or ""
        if full_tag not in content:
            continue
        new_content = content.replace(full_tag, replacement, 1)
        new_sections[idx] = section.model_copy(update={"content": new_content})
        applied.append(f'"{section.title}": resolved "{full_tag[:60]}…" from another section')

    if not applied:
        return draft, []

    updated = draft.model_copy(update={"sections": new_sections})
    return updated, applied
