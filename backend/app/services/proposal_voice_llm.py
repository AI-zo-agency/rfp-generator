"""LLM voice pass: find sentences that break the writing standards and rewrite them.

Replaces the regex scrub in proposal_voice_enforcement. The section is split into
paragraphs (a table is one block). Each block goes to the model with the full
section as cached context, and the model returns an edit list,
[{find, replace, rule, severity}]. Code applies a "hard" edit with str.replace only
when ``find`` is one verbatim, unique span. Text the model doesn't flag stays
byte-identical, so tables, [VERIFY] / [MANUAL FILL] tags and headings can't be
reformatted, and approved copy isn't churned. "soft" edits (tense, preference) are
reported in ``VoiceResult.suggested`` and never applied.

The rules come from the standards file passed in ``standards`` (default: the
active revision from proposal_brand_voice). Nothing here names a revision.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal

from app.services import llm
from app.services.proposal_brand_voice import load_writing_standards
from app.services.proposal_voice_enforcement import hard_voice_ban_hits

logger = logging.getLogger(__name__)

Register = Literal["narrative", "procurement", "cover_letter"]

NODE_NAME = "proposal_voice_llm"
_MAX_CONCURRENT_CALLS = 8
_MIN_WORDS = 4  # "Warmly", "Signature: ..." and similar stubs aren't worth a call
_sem: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None  # one per event loop

_INSTRUCTIONS = """You are the voice editor for zö agency proposals. The standards file above governs; follow it exactly.

You get the full section for context and one block from it to review. Edit only that block. Find every sentence or clause in it that breaks a rule in the standards and rewrite it. Return only the edits.

Look for:
- The six patterns in Section 2, in any form: negation-contrast (including standalone denials such as "we won't claim one"), significance close (a trailing clause such as "which is why...", "which is what...", "the same discipline X already runs"), enumerative throat-clear, editorial heading, abstract verb (an engagement, document, budget, or plan doing what only people do: "that engagement taught us", "the scope holds the risk"), and self-reference (the document announcing itself: "we won't repeat that here", "what we'll add instead").
- Em dashes anywhere, including table cells. Use a comma, a colon, a period, or a new sentence, whichever reads right.
- Empty words, process verbs (scope, benchmark, action, resource used as verbs), performative openers, writing for effect (punchlines, crafted repetition, fragments for emphasis), rhetorical hedging ("we think", "hopefully").
- Narrating the act of speaking or a gap: "we'll say plainly", "we won't claim one to round out this response". State the fact once, flat, and say what we'll do. If the full section already states the same gap elsewhere, cut the repeat.
- Text that is an instruction or to-do addressed to the writer instead of the evaluator ("Bid that record accurately.", "we will update this table before submitting"). Delete it or rewrite it as a statement to the reader.
- Broken sentences: dangling words, a fragment left where a clause was cut ("prepared to demonstrate, overstating."), unbalanced parentheses, a missing object ("include ."), a duplicated word or title. Repair only what the surrounding text makes certain. When words are missing and the section doesn't say what they were, delete the broken clause. Never guess.
- Uncontracted first-person forms ("We will commit to" becomes "We'll commit to"). Contract the words only. Changing present tense to future tense is never a contraction fix; report it as soft.

Precision over recall. This may be copy that is already approved. Flag only clear violations. If you are unsure, leave it alone. Never flag a rewrite you would merely prefer.

Severity, on every edit:
- "hard": a clear break of a rule listed above.
- "soft": a judgment call, including changing present tense to future tense. Soft edits are shown to a human and are never applied automatically.

What stays, even though it looks like a violation:
- Commercial qualification and contract terms: conditions, exclusions, "only upon the County's written approval", "not subject to adjustment", "there is no separate reimbursable-expense category", "no unresolved violations". These mark the edge of a promise. They are not negation-contrast.
- Cross-references to a named exhibit, section, or page ("Section 3", "Exhibit C carries the reference fields"). Only pointing at the document's own act of speaking is self-reference.
- Present or perfect tense for completed facts and standing capability ("has reviewed", "operates as", "has not posted any addenda").
- Labels, form fields, and run-in headings. Fix wording in a label only if it breaks a hard rule. Never add tense or contractions to a label.
- A denial that is a fact: rewrite it as an affirmative statement of the same fact ("we won't ask the County to carry that liability" becomes "the County carries none of that liability"). Delete only meta-commentary and leaked instructions.

What never to change:
- The strength of a claim. "Gave us practice unifying" cannot become "we unified". A hedge that reflects a real limit ("closest match", "have not yet") stays as strong as it was. Never turn experience into an accomplishment, and never drop a commitment.
- Facts: names, numbers, dates, dollar amounts, phone numbers, emails, addresses, solicitation numbers, titles, client names, counts. Do not add any fact, claim, credential, or number that is not already in the section. A stated gap (for example no HIPAA-covered engagement) stays stated, once, in plain words.
- [VERIFY: ...] and [MANUAL FILL: ...] tags, markdown headings, table separator rows, and any tagline the standards' exceptions register names.
- Words of belief or expectation ("we don't expect", "we believe", "we plan to", "we see", "should") are part of the claim. Never replace them with a flat statement of fact ("there will be no delay"). A sentence like "we see three programs that are ready" must not become "Three programs are ready". Keep the expectation or the view, or leave the sentence alone.
- Counts and named quantities ("five checkpoints, five approvals", "three rounds") are facts and commitments. Keep every one when you rewrite a sentence, even when you shorten it.
- A list stays a list. When you repair its punctuation, change only the punctuation. Do not add words such as "including" or "such as" that change what the list means.
- Certifications, attestations, acknowledgements, disclosure statements, and legal clauses the firm signs (sentences that say "zö agency certifies", "acknowledges", "represents", "agrees", or that sit inside a signed form) are RFP-mandated wording. Leave their tense, contractions, and phrasing alone. Fix only a broken sentence or an em dash there.
- Sentences that already follow the rules. Leave them out. If nothing needs fixing, return an empty list.

How to write each edit:
- "find" is copied character for character from the block, one sentence or one clause, long enough to be unique. Never include a newline or a table pipe unless the fix is inside one table cell, in which case find is only the text inside that cell.
- "replace" is the whole rewritten sentence or clause, in plain American English, contractions, no em dashes. Use "" to delete a sentence that adds nothing.
- Keep the first-person voice the section already uses. Keep the length the same or shorter.
- "rule" is a short label such as negation-contrast, significance-close, self-reference, em-dash, broken-sentence, instruction-leak, hedge, process-verb, throat-clear, abstract-verb, contraction, tense.
"""

_COVER_LETTER_EXTRA = """
This section is a signed cover letter. It follows the correspondence standard in Section 3: address the recipient by first name (drop honorifics such as "Mr." or "Ms."; use the first name that appears in the section), keep the warmth a person would say aloud, and fix duplicated titles in the signature block.
"""

_PROCUREMENT_EXTRA = """
This section is a formal procurement response. Keep it human without going casual. Answer in the order asked. Leave form labels, exhibit titles, and RFP-mandated wording alone.
"""

_VERIFY_INSTRUCTIONS = """You audit proposed voice edits for zö agency proposals. The standards file above governs.

Each edit below has an ORIGINAL span, a REPLACEMENT, and the rule the editor cited. The editor already decided the ORIGINAL breaks a rule. Do not second-guess that. Decide one thing: is the REPLACEMENT faithful?

"faithful" is true when the REPLACEMENT keeps every fact and every promise of future work, service, or deliverable from the ORIGINAL, adds nothing new, and leaves every claim about past experience exactly as strong as it was.

Faithful: removing a disclaimer about our own wording ("and we won't claim one"); removing a trailing "which is why..." or "the same discipline X" clause; deleting a cut-off fragment with no readable meaning; deleting a to-do about editing this document before it is submitted while keeping the client-facing commitment; contracting words outside a certification or legal clause the firm signs; swapping a process verb for a plain verb of the same meaning.

Not faithful: "gave us practice" becoming "we did"; dropping a promise ("we'll pair a media buyer with a specialist"); guessing a missing object or adding a commitment that was not there; adding a number, name, or claim; turning an expectation or hedge into a flat statement of fact ("we don't expect any delay" becoming "there will be no delay"); turning a statement of our own view or assessment into a flat statement of fact ("we see two options that fit" becoming "two options fit"); dropping a count or a number of commitments ("five checkpoints, five approvals" becoming "five-checkpoint"); changing the wording of a certification or legal clause the firm signs; adding a word that changes the meaning of a list.

Return JSON: {"verdicts": [{"i": 0, "faithful": true, "reason": "short"}]} with one verdict per edit, using the edit numbers given."""

_OUTPUT_FORMAT = '\nReturn JSON: {"edits": [{"find": "...", "replace": "...", "rule": "...", "severity": "hard"}]}'


@dataclass
class VoiceEdit:
    find: str
    replace: str
    rule: str
    severity: str = "soft"


@dataclass
class VoiceResult:
    text: str
    applied: list[VoiceEdit] = field(default_factory=list)
    suggested: list[VoiceEdit] = field(default_factory=list)
    rejected: list[tuple[VoiceEdit, str]] = field(default_factory=list)
    model: str = ""
    skipped: str = ""
    reviewed: list[str] = field(default_factory=list)

    @property
    def logs(self) -> list[str]:
        out = [f"Voice LLM: {e.rule}: {e.find[:60]!r}" for e in self.applied]
        out += [f"Voice LLM rejected ({why}): {e.find[:60]!r}" for e, why in self.rejected]
        return out


def _get_sem() -> asyncio.Semaphore:
    global _sem
    loop = asyncio.get_running_loop()
    if _sem is None or _sem[0] is not loop:
        _sem = (loop, asyncio.Semaphore(_MAX_CONCURRENT_CALLS))
    return _sem[1]


def _tokens(text: str) -> list[str]:
    """Words and numbers, punctuation stripped. No regex."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text)
    return cleaned.split()


def block_hash(block: str, rev_id: str = "") -> str:
    """Stable id for a reviewed paragraph. The revision is part of the hash, so a
    paragraph reviewed under one revision is unreviewed under another."""
    return hashlib.sha256(f"{rev_id}\n{block}".encode("utf-8")).hexdigest()[:16]


def _new_fact_tokens(replacement: str, section_text: str) -> list[str]:
    """Tokens in ``replacement`` that could be a new fact.

    Numbers are strict: any number not in the section is new. Capitalized words are
    checked against the section case-insensitively, except the first word of the
    replacement (sentence case says nothing about a name).
    """
    known = {t.casefold() for t in _tokens(section_text)}
    new: list[str] = []
    for i, tok in enumerate(_tokens(replacement)):
        if tok.casefold() in known:
            continue
        has_digit = any(ch.isdigit() for ch in tok)
        if has_digit or (tok[0].isupper() and i > 0):
            new.append(tok)
    return new


def _protected(span: str) -> bool:
    return "[VERIFY" in span or "[MANUAL FILL" in span or "[DESIGNER NOTE" in span


def _check_edit(edit: VoiceEdit, text: str, block: str) -> str:
    """Return "" when the edit is safe to apply, else the reason it isn't."""
    if not edit.find.strip():
        return "empty find"
    if edit.find == edit.replace:
        return "no change"
    if edit.find not in block:
        return "find not in the reviewed block"
    if text.count(edit.find) != 1:
        return f"find matched {text.count(edit.find)} times"
    if _protected(edit.find) or _protected(edit.replace):
        return "touches a tag"
    if "\n" in edit.find and "|" in edit.find:
        return "spans table rows"
    new = _new_fact_tokens(edit.replace, text)
    if new:
        return f"adds tokens not in section: {new}"
    return ""


def _parse_edits(raw: object) -> list[VoiceEdit]:
    if not isinstance(raw, dict):
        return []
    items = raw.get("edits")
    if not isinstance(items, list):
        return []
    out: list[VoiceEdit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        find, replace = item.get("find"), item.get("replace")
        if isinstance(find, str) and isinstance(replace, str):
            sev = "hard" if item.get("severity") == "hard" else "soft"
            out.append(VoiceEdit(find, replace, str(item.get("rule") or "voice"), sev))
    return out


def _apply(text: str, edit: VoiceEdit) -> str:
    """Swap one verbatim span; a deletion also takes one neighboring space."""
    if edit.replace:
        return text.replace(edit.find, edit.replace, 1)
    for span in (" " + edit.find, edit.find + " ", edit.find):
        if span in text:
            return text.replace(span, "", 1)
    return text


def _blocks(text: str) -> list[str]:
    """Paragraphs worth a model call. A table is one block."""
    out: list[str] = []
    for block in text.split("\n\n"):
        b = block.strip()
        if not b or b.startswith("#") or _protected(b) and len(b) < 200:
            continue
        if len(b.split()) < _MIN_WORDS:
            continue
        out.append(b)
    return out


async def _review_block(
    block: str,
    *,
    instructions: str,
    prefix: list[str],
    rfp_id: str | None,
) -> tuple[list[VoiceEdit], str, str]:
    """One model call. Returns (edits, model, error)."""
    async with _get_sem():
        try:
            raw, model = await llm.chat_json(
                [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": f"Block to review:\n\n{block}"},
                ],
                max_tokens=4000,
                temperature=0.0,
                tier="light",
                reasoning_effort="low",
                node_name=NODE_NAME,
                rfp_id=rfp_id,
                cache_prefix=prefix,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Voice LLM block failed: %s", str(exc)[:200])
            return [], "", f"llm error: {str(exc)[:120]}"
    return _parse_edits(raw), model, ""


async def _verify(
    cands: list[tuple[VoiceEdit, str]],
    *,
    prefix: list[str],
    rfp_id: str | None,
) -> dict[int, tuple[bool, str]]:
    """One call for a section's hard edits. Missing verdicts count as rejected."""
    listing = "\n\n".join(
        f"Edit {i}\nRule: {e.rule}\nORIGINAL: {e.find}\nREPLACEMENT: {e.replace or '(deleted)'}"
        for i, (e, _) in enumerate(cands)
    )
    async with _get_sem():
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": _VERIFY_INSTRUCTIONS},
                {"role": "user", "content": listing},
            ],
            max_tokens=4000,
            temperature=0.0,
            tier="light",
            reasoning_effort="low",
            node_name=NODE_NAME + "_verify",
            rfp_id=rfp_id,
            cache_prefix=prefix,
        )
    out: dict[int, tuple[bool, str]] = {}
    items = raw.get("verdicts") if isinstance(raw, dict) else None
    for v in items if isinstance(items, list) else []:
        if isinstance(v, dict) and isinstance(v.get("i"), int):
            ok = v.get("faithful") is True
            out[v["i"]] = (ok, str(v.get("reason") or ""))
    return out


async def rewrite_for_voice(
    text: str,
    *,
    register: Register = "narrative",
    apply: bool = True,
    standards: str | None = None,
    rfp_id: str | None = None,
    rev_id: str = "",
    reviewed: Collection[str] = (),
) -> VoiceResult:
    """Fix voice violations in ``text``. With ``apply=False`` only report them.

    On any LLM failure the affected block is left unchanged and ``skipped`` is set;
    there is no regex fallback.
    """
    if not (text or "").strip():
        return VoiceResult(text=text or "")
    if not llm.is_configured():
        return VoiceResult(text=text, skipped="llm not configured")

    instructions = _INSTRUCTIONS
    if register == "cover_letter":
        instructions += _COVER_LETTER_EXTRA
    elif register == "procurement":
        instructions += _PROCUREMENT_EXTRA
    instructions += _OUTPUT_FORMAT
    prefix = [
        standards or load_writing_standards(),
        f"Full section, for context only. Edit nothing outside the block you are given:\n\n{text}",
    ]

    skip = set(reviewed)
    # Re-open a previously reviewed paragraph when a hard ban is still in it —
    # otherwise a miss by the LLM freezes the violation forever.
    blocks = [
        b
        for b in _blocks(text)
        if block_hash(b, rev_id) not in skip or hard_voice_ban_hits(b)
    ]
    if not blocks:
        return VoiceResult(text=text)
    reviews = await asyncio.gather(
        *(_review_block(b, instructions=instructions, prefix=prefix, rfp_id=rfp_id) for b in blocks)
    )

    result = VoiceResult(text=text)
    errors: list[str] = []
    failed: set[int] = set()
    cands: list[tuple[VoiceEdit, int]] = []  # hard edits that pass the local guards
    for bi, (edits, model, err) in enumerate(reviews):
        result.model = result.model or model
        if err:
            errors.append(err)
            failed.add(bi)
        for edit in edits:
            if edit.severity != "hard":
                result.suggested.append(edit)
                continue
            why = _check_edit(edit, text, blocks[bi])
            if why:
                result.rejected.append((edit, why))
            else:
                cands.append((edit, bi))

    verdicts: dict[int, tuple[bool, str]] = {}
    if cands:
        try:
            verdicts = await _verify(
                [(e, blocks[bi]) for e, bi in cands], prefix=prefix, rfp_id=rfp_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Voice LLM verify failed: %s", str(exc)[:200])
            errors.append(f"verify error: {str(exc)[:120]}")
            failed |= {bi for _, bi in cands}
        # a candidate the verifier said nothing about is unreviewed, so it is retried next pass
        failed |= {bi for i, (_, bi) in enumerate(cands) if i not in verdicts}

    out = text
    finals = list(blocks)
    for i, (edit, bi) in enumerate(cands):
        ok, reason = verdicts.get(i, (False, "no verdict"))
        if not ok:
            result.rejected.append((edit, f"verifier: {reason}"))
            continue
        why = _check_edit(edit, out, blocks[bi])  # an earlier edit may have moved the span
        if why:
            result.rejected.append((edit, why))
            continue
        result.applied.append(edit)
        if apply:
            out = _apply(out, edit)
            finals[bi] = _apply(finals[bi], edit)
    result.text = out
    if apply:
        # Only hash as reviewed when the paragraph is actually clean. An empty
        # edit list on a dirty block must not poison voice_reviewed.
        result.reviewed = [
            block_hash(f, rev_id)
            for bi, f in enumerate(finals)
            if bi not in failed and not hard_voice_ban_hits(f)
        ]
    if errors:
        result.skipped = f"{len(errors)} call(s) failed: {errors[0]}"
    return result
