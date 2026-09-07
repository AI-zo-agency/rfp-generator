"""Deterministic narrative voice enforcement — fixes Vendor register regardless of LLM model."""

from __future__ import annotations

import re

from app.services.proposal_brand_voice import Register, classify_section_register

_PROCUREMENT_ENTITY = re.compile(
    r"\b([Tt])he (Vendor|Offeror|Proposer|Respondent|Contractor)('s)?\b"
)
_AGENCY_THIRD = re.compile(r"\b([Tt])he agency('s)?\b")
_FIRM_THIRD = re.compile(r"\b([Tt])he firm('s)?\b")
_ZO_THIRD = re.compile(
    r"\bzö agency (?:delivers|brings|provides|maintains|confirms|offers|has|is)\b",
    re.IGNORECASE,
)

_SUBSECTION_VENDOR_HEADER = re.compile(
    r"(\d+(?:\.\d+)*\s+)(Vendor Identification\b)",
    re.IGNORECASE,
)

_STATIC_RFP_DUPLICATE_RES = (
    re.compile(r"section\s*1\b", re.IGNORECASE),
    re.compile(r"company\s+overview", re.IGNORECASE),
    re.compile(r"section\s*2\b", re.IGNORECASE),
    re.compile(r"team\s+(overview|bios|qualifications|experience)", re.IGNORECASE),
    re.compile(r"section\s*3\b", re.IGNORECASE),
    re.compile(r"(case\s+stud|our\s+work|past\s+performance|relevant\s+experience)", re.IGNORECASE),
)

# Titles fully owned by zö static Sections 1–3 — do not draft again in Phase 3.
_STATIC_COVERED_TITLE_RES = (
    re.compile(r"\bwho\s+we\s+are\b", re.IGNORECASE),
    re.compile(r"\bour\s+promise\b", re.IGNORECASE),
    re.compile(r"\bcompany\s+history\b", re.IGNORECASE),
    re.compile(r"\bfirm\s+history\b", re.IGNORECASE),
    re.compile(r"\bfirm\s+(?:overview|profile|background)\b", re.IGNORECASE),
    re.compile(r"\babout\s+(?:the\s+)?(?:firm|agency|company|proposer|vendor)\b", re.IGNORECASE),
    re.compile(r"\bclient\s+roster\b", re.IGNORECASE),
    re.compile(r"\bcore\s+services\b", re.IGNORECASE),
    re.compile(r"\borganizational?\s+structure\b", re.IGNORECASE),
    re.compile(r"\bbusiness\s+information\b", re.IGNORECASE),
    # Bare "Company Information" essay — owned by 1.3. Offeror/Vendor Identification
    # *forms* stay in the outline (buyer needs the form) but are compressed at draft/scan.
    re.compile(r"^\s*company\s+information\s*$", re.IGNORECASE),
    # Agency / firm CERTIFICATIONS list owned by Section 1.4 — NOT signature
    # packet rows like "Certification of Proposal" / bidder certification forms.
    re.compile(
        r"(?i)^\s*(?:\d+(?:\.\d+)*\s*[.—–\-:]?\s*)?"
        r"(?:agency\s+|firm\s+|company\s+|business\s+)?"
        r"certifications?\s*$"
    ),
    re.compile(
        r"(?i)\b(?:agency|firm|company|business)\s+certifications?\b"
    ),
    re.compile(r"(?i)\blicenses?\s+(?:and|&)\s+certifications?\b"),
    re.compile(r"\binsurance\s+information\b", re.IGNORECASE),
    # Coverage narrative / COI delivery is owned by Section 1.5 — do not draft a
    # second essay under "Certificate of Insurance" in Phase 3.
    re.compile(r"\bcertificate(?:s)?\s+of\s+insurance\b", re.IGNORECASE),
    re.compile(r"\bproof\s+of\s+insurance\b", re.IGNORECASE),
    re.compile(r"\binsurance\s+certificate(?:s)?\b", re.IGNORECASE),
    re.compile(
        r"^\s*(?:coi|insurance\s+coverage|liability\s+insurance)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\bcompany\s+overview\b", re.IGNORECASE),
    # RFP TOC "Company Background" is Sections 1.1–1.5, not a second essay.
    re.compile(r"\bcompany\s+background\b", re.IGNORECASE),
    # Section 2 owns full bios — including RFP TOC titles that restate Team Overview
    # with Contract Manager / POC / Personnel Bios/Resumes.
    re.compile(
        r"\bteam\s+overview\b(?:\s*[—\-–:].*)?\b("
        r"bios?|resumes?|personnel|contract\s+manager|point\s+of\s+contact|"
        r"primary\s+contact|staff(?:ing)?"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*team\s+overview\s*$", re.IGNORECASE),
    re.compile(r"\bpersonnel\s+bios?(?:\s*/\s*resumes?)?\b", re.IGNORECASE),
    re.compile(r"\b(?:staff|team)\s+(?:member\s+)?(?:bios?|resumes?)\b", re.IGNORECASE),
    # Bios-only tabs (Section 2) — do NOT match scored RFP headings like
    # "Qualifications and Experience of the Firm and Key Personnel".
    re.compile(r"^\s*(?:key\s+personnel|team\s+bios?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*staff(?:ing)?\s+(?:bios|resumes|qualifications)\s*$", re.IGNORECASE),
)

# RFP tables of contents often restate a requirement as a whole SENTENCE rather
# than a label: "A brief description of the firm, including the year the firm
# was established, type of firm (partnership, corporation, etc.)". These asks
# were previously matched by _STATIC_COVERED_TITLE_RES and dropped on the
# ASSUMPTION that static Section 1.3 answers them — but static sections are
# generated before Phase 2, never see the RFP, and nothing verified the
# delegation actually landed. They are kept separate here because, unlike the
# label patterns above (a title that names a whole static section 1:1), these
# ask a specific factual question that only the static section's own TEXT can
# answer — see ``static_section_covers_requirement``.
_UNVERIFIED_STATIC_DELEGATION_RES = (
    re.compile(
        r"\b(?:brief\s+)?description\s+of\s+(?:the\s+)?(?:firm|agency|company|"
        r"organi[sz]ation|proposer|vendor)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byear\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
        r"(?:established|founded|formed|incorporated)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdate\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
        r"(?:established|founded|formed|incorporated)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btype\s+of\s+(?:firm|entity|organi[sz]ation)\b", re.IGNORECASE),
    re.compile(r"\bform\s+of\s+(?:business|organi[sz]ation)\b", re.IGNORECASE),
    re.compile(r"\blegal\s+(?:structure|entity|form|status)\b", re.IGNORECASE),
    re.compile(
        r"\byears?\s+in\s+business\b|\bhow\s+long\b.{0,40}\bin\s+business\b",
        re.IGNORECASE,
    ),
)

# Answer-shaped signal each unverified-delegation ask requires in the static
# section's own text before it can be treated as covered.
_FIRM_TYPE_ANSWER_RE = re.compile(
    r"\b(corporation|corp\.?|l\.?l\.?c\.?|partnership|sole\s+proprietorship|"
    r"s-?corp(?:oration)?|c-?corp(?:oration)?|nonprofit|non-profit|"
    r"limited\s+liability)\b",
    re.IGNORECASE,
)
_FOUNDING_FACT_ANSWER_RE = re.compile(
    r"\b(19|20)\d{2}\b|\b(established|founded|formed|incorporated|since)\b",
    re.IGNORECASE,
)

# ``None`` = this ask has NO answer shape to look for, so it can never be
# proven mechanically. An earlier revision used a ">= 200 characters of prose"
# heuristic here; that is failing open — 200 characters of entirely unrelated
# prose passes it — which recreates the exact "assume the delegation landed"
# defect this module exists to stop. An open-ended "describe the firm" is now
# never auto-satisfied on its own. It can only be discharged through the
# specific sub-asks it enumerates ("...including the year the firm was
# established, type of firm (partnership, corporation, etc.)"), each of which
# is checked on its own answer shape below.
_DELEGATION_PROOF_CHECKS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (
        re.compile(
            r"\b(?:brief\s+)?description\s+of\s+(?:the\s+)?(?:firm|agency|company|"
            r"organi[sz]ation|proposer|vendor)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        re.compile(
            r"\byear\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
            r"(?:established|founded|formed|incorporated)\b",
            re.IGNORECASE,
        ),
        _FOUNDING_FACT_ANSWER_RE,
    ),
    (
        re.compile(
            r"\bdate\s+(?:the\s+)?(?:firm|agency|company|business)\s+was\s+"
            r"(?:established|founded|formed|incorporated)\b",
            re.IGNORECASE,
        ),
        _FOUNDING_FACT_ANSWER_RE,
    ),
    (
        re.compile(r"\btype\s+of\s+(?:firm|entity|organi[sz]ation)\b", re.IGNORECASE),
        _FIRM_TYPE_ANSWER_RE,
    ),
    (
        re.compile(r"\bform\s+of\s+(?:business|organi[sz]ation)\b", re.IGNORECASE),
        _FIRM_TYPE_ANSWER_RE,
    ),
    (
        re.compile(r"\blegal\s+(?:structure|entity|form|status)\b", re.IGNORECASE),
        _FIRM_TYPE_ANSWER_RE,
    ),
    (
        re.compile(
            r"\byears?\s+in\s+business\b|\bhow\s+long\b.{0,40}\bin\s+business\b",
            re.IGNORECASE,
        ),
        _FOUNDING_FACT_ANSWER_RE,
    ),
)


def static_section_covers_requirement(requirement_text: str, static_section_text: str) -> bool:
    """Real proof, not an assumption: true only when the static section's own
    text names the specific fact the requirement asks for (entity type,
    founding year, ...) rather than merely sharing a topic label with it.

    Observed: "type of firm" was deleted from the outline whenever the RFP
    text merely contained that phrase, with no check that static Section 1.3
    ever stated whether zö is an LLC, corporation, etc.

    A compound RFP ask enumerates several facts at once ("a brief description
    of the firm, including the year the firm was established, type of firm..."),
    so **every** enumerated fact with a checkable answer shape must appear in
    the static text — proving one of three does not discharge the other two.
    An ask whose only match is the open-ended "describe the firm" pattern has
    nothing checkable and is never auto-satisfied.
    """
    text = (requirement_text or "").strip()
    haystack = static_section_text or ""
    if not text or not haystack.strip():
        return False
    required_answers = [
        answer_re
        for ask_re, answer_re in _DELEGATION_PROOF_CHECKS
        if answer_re is not None and ask_re.search(text)
    ]
    if not required_answers:
        # Either this is not a delegation ask at all, or its only match was the
        # unprovable open-ended one — fail closed, keep the requirement visible.
        return False
    return all(answer_re.search(haystack) for answer_re in required_answers)


def contains_vendor_language(content: str) -> bool:
    return bool(
        _PROCUREMENT_ENTITY.search(content)
        or _AGENCY_THIRD.search(content)
        or _FIRM_THIRD.search(content)
    )


def _swap_entity(match: re.Match[str]) -> str:
    cap = match.group(1)
    possessive = match.group(3)
    if possessive:
        return "Our" if cap == "T" else "our"
    return "We" if cap == "T" else "we"


def _swap_agency_firm(match: re.Match[str]) -> str:
    cap = match.group(1)
    possessive = match.group(2)
    if possessive:
        return "Our" if cap == "T" else "our"
    return "We" if cap == "T" else "we"


_WE_VERB_AGREEMENT = re.compile(
    r"\bWe (is|was|confirms|maintains|operates|has|brings|delivers|provides|"
    r"offers|includes|submits)\b",
    re.IGNORECASE,
)

_VERB_TO_PLURAL = {
    "is": "are",
    "was": "were",
    "confirms": "confirm",
    "maintains": "maintain",
    "operates": "operate",
    "has": "have",
    "brings": "bring",
    "delivers": "deliver",
    "provides": "provide",
    "offers": "offer",
    "includes": "include",
    "submits": "submit",
}


def _fix_we_verb_agreement(text: str) -> str:
    def fix(match: re.Match[str]) -> str:
        verb = match.group(1)
        fixed = _VERB_TO_PLURAL.get(verb.lower(), verb.lower())
        if verb[0].isupper():
            fixed = fixed.capitalize()
        return f"We {fixed}"

    return _WE_VERB_AGREEMENT.sub(fix, text)


# Rev 3 empty hype + generic AI filler — deterministic strip on persist/generate.
_BANNED_HYPE_WORD_RES = tuple(
    re.compile(rf"\b{word}\b", re.IGNORECASE)
    for word in (
        "nice",
        "great",
        "amazing",
        "incredible",
        "exciting",
        "passionate",
        "robust",
        "seamless",
        "leverage",
        "elevate",
        "unlock",
        "impactful",
    )
)
_BANNED_HYPE_SOLUTION_RES = re.compile(r"\b(?:our|the|a|an)\s+solutions?\b", re.IGNORECASE)
_GENERIC_AI_OPENERS_RES = (
    re.compile(r"^\s*At the end of the day,?\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Here(?:'s|\s+is)\s+the\s+thing:?\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Let me be clear:?\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^\s*(?:I(?:'m|\s+am)\s+thrilled\s+to|I\s+hope\s+this\s+finds\s+you\s+well),?\s*",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^\s*I\s+wanted\s+to\s+reach\s+out\s+(?:to\s+)?", re.IGNORECASE | re.MULTILINE),
)
_NOT_X_ITS_Y_RES = re.compile(
    r"\b(?:not|isn't|is not|wasn't|was not|don't just|do not just)\s+[^,.;]{1,80}?"
    r"(?:,\s*)?(?:it(?:'s| is| was)|they(?:'re| are)|we(?:'re| are))\s+",
    re.IGNORECASE,
)

# Rev 6 pattern shapes — deterministic strip on every narrative path + persist.
_TAGLINE_EXEMPT_RE = re.compile(
    r"we are more than your agency\.?\s*we are your strongest advocate\.?",
    re.IGNORECASE,
)
# "We'd rather X than Y" → keep X as the affirmative commitment.
_WED_RATHER_THAN_RE = re.compile(
    r"\bwe(?:'d| would)\s+rather\s+(.+?)\s+than\b[^.;\n]{0,180}",
    re.IGNORECASE | re.DOTALL,
)
# "X rather than Y" → drop the contrast tail (keep X).
_RATHER_THAN_TAIL_RE = re.compile(
    r"\s+rather than\b[^.;\n]{0,180}",
    re.IGNORECASE,
)
_NEGATION_INSTEAD_OF_RE = re.compile(
    r"\s+instead of\b[^.;\n]{0,160}",
    re.IGNORECASE,
)
# "X, not Y" / "X; not Y" — any trailing contrast after comma/semicolon.
_NEGATION_X_NOT_Y_RE = re.compile(
    r"[,;]\s+not\s+(?:a|an|the|just|only|merely|simply\s+)?[^.;\n]{0,160}",
    re.IGNORECASE,
)
_NEGATION_PHRASE_RES = (
    re.compile(r"\bnot just\b", re.IGNORECASE),
    re.compile(r"\bnot only\b", re.IGNORECASE),
    re.compile(r"\bnot simply\b", re.IGNORECASE),
    re.compile(r"\bnot merely\b", re.IGNORECASE),
    re.compile(r"\bisn't just\b", re.IGNORECASE),
    re.compile(r"\bisn't only\b", re.IGNORECASE),
    re.compile(r"\bwasn't just\b", re.IGNORECASE),
    re.compile(r"\bdon't just\b", re.IGNORECASE),
    re.compile(r"\bdoesn't just\b", re.IGNORECASE),
    re.compile(r"\bdo not just\b", re.IGNORECASE),
    re.compile(r"\bmore than just\b", re.IGNORECASE),
    re.compile(r"\bbeyond just\b", re.IGNORECASE),
    re.compile(r"\bless about\b.{0,40}?\bthan\b", re.IGNORECASE),
)
_SIGNIFICANCE_CLOSE_SENTENCE_RES = (
    re.compile(
        r"(?i)([.!?]\s+)?That's the kind of\b[^.!?\n]{0,200}[.!]?",
    ),
    re.compile(
        r"(?i)([.!?]\s+)?That is the kind of\b[^.!?\n]{0,200}[.!]?",
    ),
    re.compile(
        r"(?i),\s+which is what makes\b[^.!?\n]{0,120}",
    ),
    re.compile(
        r"(?i)\s+that runs through every\b[^.!?\n]{0,80}",
    ),
)
_HEDGE_ANNOUNCE_RES = (
    re.compile(
        r"(?i)([.!?]\s+)?That's a real tradeoff worth naming:?\s*",
    ),
    re.compile(
        r"(?i)([.!?]\s+)?(?:It(?:'s| is)\s+)?worth noting(?:\s+that)?:?\s*",
    ),
    re.compile(
        r"(?i)([.!?]\s+)?(?:It(?:'s| is)\s+)?worth mentioning(?:\s+that)?:?\s*",
    ),
    re.compile(
        r"(?i)([.!?]\s+)?Keep in mind(?:\s+that)?:?\s*",
    ),
)
_CASE_STUDY_FALSE_FRAMING_RES = (
    re.compile(
        r"(?i)\s+instead of starting from a blank page\.?",
    ),
    re.compile(
        r"(?i)\ban existing asset we (?:built|worked) on\b",
    ),
    re.compile(
        r"(?i)\bbuilt on (?:an |what a client already has as an )?existing asset\b",
    ),
)
# Dangling sentence-final "before." (dropped continuation).
_DANGLING_BEFORE_RE = re.compile(r"\s+before\s*(?=[.!?…]|$)", re.IGNORECASE)


_PLACEHOLDER_TAG_RE = re.compile(
    r"\[(?:VERIFY|MANUAL FILL|FLAG|TBD|INSERT|DESIGNER NOTE)[^\]]*\]",
    re.IGNORECASE,
)


def _scrub_rev6_fragment(text: str, logs: list[str]) -> str:
    """Apply Rev 6 voice bans to a prose fragment (paragraph or table cell)."""
    if not (text or "").strip():
        return text

    placeholders: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00TAG{len(placeholders) - 1}\x00"

    # Protect tagline + VERIFY / MANUAL FILL spans — "not in KB" inside a tag
    # must not be treated as negation-contrast (destroyed Review VERIFY flags).
    out = _TAGLINE_EXEMPT_RE.sub(_stash, text)
    out = _PLACEHOLDER_TAG_RE.sub(_stash, out)

    def _wed_rather_keep(m: re.Match[str]) -> str:
        kept = (m.group(1) or "").strip(" ,;")
        if not kept:
            logs.append("Rev6: removed rather-than clause")
            return ""
        logs.append("Rev6: scrubbed we'd-rather-than negation-contrast")
        if kept[0].islower():
            kept = kept[0].upper() + kept[1:]
        # Prefer future-tense commitment when the scrap starts with a bare verb.
        if re.match(r"(?i)^(spend|run|build|keep|ship|fix)\b", kept):
            kept = f"We'll {kept[0].lower() + kept[1:]}"
        return kept

    if _WED_RATHER_THAN_RE.search(out):
        out = _WED_RATHER_THAN_RE.sub(_wed_rather_keep, out)

    if _RATHER_THAN_TAIL_RE.search(out):
        out = _RATHER_THAN_TAIL_RE.sub("", out)
        logs.append("Rev6: scrubbed rather-than negation-contrast")

    if _NEGATION_INSTEAD_OF_RE.search(out):
        out = _NEGATION_INSTEAD_OF_RE.sub("", out)
        logs.append("Rev6: scrubbed instead-of negation-contrast")

    if _NEGATION_X_NOT_Y_RE.search(out):
        out = _NEGATION_X_NOT_Y_RE.sub("", out)
        logs.append("Rev6: scrubbed X-not-Y negation-contrast")

    for pat in _NEGATION_PHRASE_RES:
        if pat.search(out):
            out = pat.sub("", out)
            logs.append("Rev6: scrubbed negation-contrast phrase")

    for pat in _SIGNIFICANCE_CLOSE_SENTENCE_RES:
        if pat.search(out):
            out = pat.sub(lambda m: m.group(1) if m.lastindex else "", out)
            logs.append("Rev6: scrubbed significance-close")

    for pat in _HEDGE_ANNOUNCE_RES:
        if pat.search(out):
            out = pat.sub(lambda m: m.group(1) if m.lastindex else "", out)
            logs.append("Rev6: scrubbed hedging announcement")

    for pat in _CASE_STUDY_FALSE_FRAMING_RES:
        if pat.search(out):
            if "existing asset" in pat.pattern.casefold():
                out = pat.sub("a prior engagement", out)
            else:
                out = pat.sub("", out)
            logs.append("Rev6: scrubbed case-study false framing")

    if _DANGLING_BEFORE_RE.search(out):
        out = _DANGLING_BEFORE_RE.sub("", out)
        logs.append("Rev6: scrubbed dangling 'before'")

    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.;:!?])", r"\1", out)
    out = re.sub(r"\s+\.", ".", out)
    out = out.strip(" ,;")

    for i, original in enumerate(placeholders):
        out = out.replace(f"\x00TAG{i}\x00", original)
    return out


def find_rev6_voice_violations(content: str) -> list[str]:
    """Detect leftover Rev 6 hard-ban patterns (Review check after scrub).

    Same shapes as ``scrub_rev6_voice_patterns`` / ZO_BRAND_AND_WRITING_STANDARDS_REV6.
    Skips MANUAL FILL / VERIFY / heading / designer-note lines and the registered
    tagline exemption. Not RFP-specific.
    """
    if not (content or "").strip():
        return []

    hits: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        if label not in seen:
            seen.add(label)
            hits.append(label)

    for line in content.splitlines():
        lead = line.lstrip()
        if (
            lead.startswith("[MANUAL FILL")
            or lead.startswith("[VERIFY")
            or lead.startswith("#")
            or lead.startswith("[DESIGNER NOTE")
        ):
            continue
        sample = _TAGLINE_EXEMPT_RE.sub("", line)
        if "—" in sample:
            _add("em dash (—)")
        if _WED_RATHER_THAN_RE.search(sample) or _RATHER_THAN_TAIL_RE.search(sample):
            _add("negation-contrast (rather than)")
        if _NEGATION_INSTEAD_OF_RE.search(sample):
            _add("negation-contrast (instead of)")
        if _NEGATION_X_NOT_Y_RE.search(sample):
            _add("negation-contrast (X, not Y)")
        for pat in _NEGATION_PHRASE_RES:
            if pat.search(sample):
                _add("negation-contrast phrase (not just / not only / …)")
                break
        for pat in _SIGNIFICANCE_CLOSE_SENTENCE_RES:
            if pat.search(sample):
                _add("significance-close")
                break
        for pat in _HEDGE_ANNOUNCE_RES:
            if pat.search(sample):
                _add("hedging announcement (worth noting / worth naming / …)")
                break
        for pat in _BANNED_HYPE_WORD_RES:
            if pat.search(sample):
                word = pat.pattern.replace(r"\b", "")
                _add(f"empty hype word ({word})")
                break
    return hits


def scrub_rev6_voice_patterns(content: str) -> tuple[str, list[str]]:
    """Deterministic Rev 6 voice bans: negation-contrast, significance-close, hedges.

    Scrubs prose lines AND markdown table cells (voice bans hide in Fee / Commitment
    tables). Skips MANUAL FILL / VERIFY / heading lines. Exempts the registered
    tagline in Section 10 of the standards file.
    """
    if not (content or "").strip():
        return content or "", []

    logs: list[str] = []
    lines_out: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        ending = "\n" if line.endswith("\n") else ""
        lead = stripped.lstrip()
        if (
            lead.startswith("[MANUAL FILL")
            or lead.startswith("[VERIFY")
            or lead.startswith("#")
        ):
            lines_out.append(line)
            continue

        # Table row: scrub each cell; keep structure.
        if lead.startswith("|"):
            # Separator rows (| --- | --- |) — leave alone.
            if re.match(r"^\s*\|[\s:|\-]+\|\s*$", stripped):
                lines_out.append(line)
                continue
            raw_cells = stripped.strip().strip("|").split("|")
            new_cells: list[str] = []
            changed = False
            for cell in raw_cells:
                original = cell.strip()
                scrubbed = _scrub_rev6_fragment(original, logs).strip()
                if scrubbed != original:
                    changed = True
                new_cells.append(scrubbed)
            if changed:
                lines_out.append("| " + " | ".join(new_cells) + " |" + ending)
            else:
                lines_out.append(line)
            continue

        text = _scrub_rev6_fragment(stripped, logs)
        lines_out.append(text + ending)

    cleaned = "".join(lines_out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    seen: set[str] = set()
    uniq_logs: list[str] = []
    for entry in logs:
        if entry not in seen:
            seen.add(entry)
            uniq_logs.append(entry)
    return cleaned, uniq_logs


def scrub_generic_ai_prose(content: str) -> str:
    """Strip rev-3 banned hype words and obvious generic-AI openers."""
    if not content.strip():
        return content

    text = content
    for pattern in _GENERIC_AI_OPENERS_RES:
        text = pattern.sub("", text)
    for pattern in _BANNED_HYPE_WORD_RES:
        text = pattern.sub("", text)
    text = _BANNED_HYPE_SOLUTION_RES.sub("", text)
    text = _NOT_X_ITS_Y_RES.sub("", text)
    text, _ = scrub_rev6_voice_patterns(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def apply_writing_standards_mechanics(content: str) -> str:
    """Deterministic Rev 6 mechanics: company name + no em dashes + voice bans."""
    if not content.strip():
        return content

    text = content
    # Em dashes / en dashes used as clause breaks → comma or hyphen for ranges.
    text = text.replace("—", ",")
    text = text.replace("–", "-")
    # Common wrong company-name spellings → zö agency
    text = re.sub(r"\bZO\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bZÖ\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bZö\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bZo\s+Agency\b", "zö agency", text)
    text = re.sub(r"\bzo\s+agency\b", "zö agency", text)
    # Cleanup double commas / spaces from dash swaps
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"[ \t]+,", ",", text)
    text, _ = scrub_rev6_voice_patterns(text)
    return text


def apply_rev6_voice_scrub_to_draft(draft: "ProposalDraft") -> tuple["ProposalDraft", list[str]]:
    """Manuscript-wide Rev 6 voice scrub for Complete Scan / ZF persist."""
    from app.models.proposal import ProposalDraft as _Draft

    if not isinstance(draft, _Draft):
        return draft, []

    logs: list[str] = []
    sections = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        if not body.strip():
            sections.append(section)
            continue
        cleaned, section_logs = scrub_rev6_voice_patterns(body)
        # Em dash + company name without a second full scrub pass.
        cleaned = cleaned.replace("—", ",")
        cleaned = cleaned.replace("–", "-")
        cleaned = re.sub(r"\bZO\s+Agency\b", "zö agency", cleaned)
        cleaned = re.sub(r"\bZÖ\s+Agency\b", "zö agency", cleaned)
        cleaned = re.sub(r"\bZö\s+Agency\b", "zö agency", cleaned)
        cleaned = re.sub(r"\bZo\s+Agency\b", "zö agency", cleaned)
        cleaned = re.sub(r"\bzo\s+agency\b", "zö agency", cleaned)
        cleaned = re.sub(r",\s*,", ",", cleaned)
        cleaned = re.sub(r"[ \t]+,", ",", cleaned)
        if cleaned != body:
            changed = True
            sections.append(section.model_copy(update={"content": cleaned}))
            for line in section_logs:
                logs.append(f"{section.id}: {line}")
            if cleaned != body and not section_logs:
                logs.append(f"{section.id}: Rev6: mechanics (em dash / company name)")
        else:
            sections.append(section)
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def apply_chat_rev6_voice_to_draft(
    draft: "ProposalDraft",
    *,
    section_ids: set[str] | list[str] | frozenset[str] | None = None,
) -> tuple["ProposalDraft", list[str]]:
    """Rev 6 enforcement before section-chat persist.

    When ``section_ids`` is provided, only those tabs are scrubbed — chat must not
    rewrite untouched bios / narrative tabs as a side effect of one Improve turn.
    When ``section_ids`` is None, every non-empty section is scrubbed (legacy /
    manuscript-wide callers).
    """
    from app.models.proposal import ProposalDraft as _Draft

    if not isinstance(draft, _Draft):
        return draft, []

    allow: set[str] | None = None
    if section_ids is not None:
        allow = {str(x) for x in section_ids if str(x).strip()}

    logs: list[str] = []
    sections = []
    changed = False
    for section in draft.sections:
        sid = section.id or ""
        if allow is not None and sid not in allow:
            sections.append(section)
            continue
        body = section.content or ""
        if not body.strip():
            sections.append(section)
            continue
        cleaned = enforce_narrative_voice(
            body,
            section_id=sid,
            title=section.title or "",
            zo_mode=getattr(section, "mode", None) or "write",
        )
        if cleaned != body:
            changed = True
            sections.append(section.model_copy(update={"content": cleaned}))
            logs.append(f"{sid}: Rev6 chat voice enforced")
        else:
            sections.append(section)
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def fix_narrative_register(content: str) -> str:
    """Rewrite third-person procurement phrasing to first-person zö voice."""
    if not content.strip():
        return content

    text = scrub_generic_ai_prose(content)
    text = apply_writing_standards_mechanics(text)
    text = _SUBSECTION_VENDOR_HEADER.sub(
        r"\1Company Identification",
        text,
    )
    text = _PROCUREMENT_ENTITY.sub(_swap_entity, text)
    text = _AGENCY_THIRD.sub(_swap_agency_firm, text)
    text = _FIRM_THIRD.sub(_swap_agency_firm, text)
    text = re.sub(r"\bOur's\b", "Our", text)
    text = re.sub(r"\bour's\b", "our", text)

    text = _ZO_THIRD.sub(
        lambda m: "We " + m.group(0).split(maxsplit=2)[-1].lower(),
        text,
    )
    text = _fix_we_verb_agreement(text)

    return text


def enforce_narrative_voice(
    content: str,
    *,
    section_id: str = "",
    title: str = "",
    zo_mode: str = "write",
    register: Register | None = None,
) -> str:
    reg = register or classify_section_register(
        section_id=section_id,
        title=title,
        zo_mode=zo_mode,
    )
    if reg != "narrative":
        return apply_writing_standards_mechanics(content)
    return fix_narrative_register(content)


def apply_compulsory_rev6_to_section(
    section: "ProposalSection",
) -> tuple["ProposalSection", list[str]]:
    """Hard Rev 6 pass on one section after any LLM edit (chat, contradiction, fill)."""
    from app.models.proposal import ProposalSection as _PS

    if not isinstance(section, _PS):
        return section, []
    body = section.content or ""
    if not body.strip():
        return section, []
    voiced = enforce_narrative_voice(
        body,
        section_id=section.id,
        title=section.title or "",
        zo_mode=getattr(section, "mode", None) or "write",
    )
    voiced, logs = scrub_rev6_voice_patterns(voiced)
    voiced = voiced.replace("—", ",").replace("–", "-")
    if voiced == body:
        return section, logs
    return section.model_copy(update={"content": voiced}), logs


def is_duplicate_static_rfp_section(
    title: str, *, static_section_text: str | None = None
) -> bool:
    """RFP-mapped sections that duplicate zö static Sections 1–3 (drafted separately).

    ``static_section_text`` is the actual drafted content of static Sections
    1–3, when available — pass it whenever it exists. Without it, a section
    that only *asks a specific factual question* (type of firm, founding year,
    ...) is never treated as covered; unlike the label patterns below, an
    unverified factual ask stays in the outline / is reported missing by the
    requirement ledger rather than being silently assumed satisfied.
    """
    t = title.strip()
    if not t:
        return False
    # Explicit company/team/identity titles → owned by static 1–3.
    if any(pattern.search(t) for pattern in _STATIC_COVERED_TITLE_RES):
        # Keep scored portfolio / sample-work tabs (need Section 3 depth).
        if re.search(
            r"\b(sample\s+work|portfolio|minimum\s+two|recent\s+campaign)\b",
            t,
            re.IGNORECASE,
        ):
            return False
        # Keep RFP-scored capability / agency-requirements matrices.
        if re.search(
            r"\b(agency\s+requirements?|capability\s+matrix|service\s+capability|"
            r"scope\s+of\s+work|statement\s+of\s+work)\b",
            t,
            re.IGNORECASE,
        ):
            return False
        # Signature / packet "Certification of …" is a buyer deliverable, never
        # Section 1.4 agency certs (MBE/DBE/etc.).
        if re.search(
            r"(?i)\bcertification\s+of\s+(?:the\s+)?"
            r"(?:proposal|bid|offer|compliance|non[-\s]?collusion)\b"
            r"|\b(?:bidder|offeror|proposer|respondent)\s+certification\b"
            r"|\bpreference\s+certification\b",
            t,
        ):
            return False
        return True
    if any(pattern.search(t) for pattern in _UNVERIFIED_STATIC_DELEGATION_RES):
        return bool(
            static_section_text
            and static_section_covers_requirement(t, static_section_text)
        )
    hits = sum(1 for pattern in _STATIC_RFP_DUPLICATE_RES if pattern.search(t))
    if hits >= 2:
        return True
    if re.search(r"section\s*[123]\b", t, re.IGNORECASE) and re.search(
        r"overview|company|team|work|case", t, re.IGNORECASE
    ):
        return True
    if re.fullmatch(
        r"section\s*1\s*[—\-–:]\s*company\s+overview",
        t,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        r"section\s*2\s*[—\-–:]\s*team\s+overview",
        t,
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(
        r"section\s*3\s*[—\-–:]\s*our\s+work.*",
        t,
        re.IGNORECASE,
    ):
        return True
    return False


def should_skip_rfp_section_as_static_duplicate(
    *,
    title: str,
    duplicate_of_static_section: str | None = None,
    evaluation_weight: float | None = None,
    static_section_text: str | None = None,
) -> bool:
    """True when intelligence/drafting must omit this tab (already covered by Sections 1–3).

    A section carrying evaluation points is never skipped here — Phase 2 named
    it a scored criterion, and dropping it produced proposals missing
    "Technical Approach" and similar scored asks. Static delegation is a
    convenience for unscored identity boilerplate, not for what evaluators
    score.
    """
    if evaluation_weight is not None:
        try:
            if float(evaluation_weight) > 0:
                return False
        except (TypeError, ValueError):
            pass
    # Cost / Fees / Budget tabs are never "static duplicate" — Phase 3.5 needs them.
    if re.search(
        r"\b("
        r"cost\s+proposal|cost\s+of(?:\s+the)?\s+base|fee\s+schedule|"
        r"price\s+proposal|pricing\s+proposal|compensation\s+schedule|"
        r"budget\s*(?:&|and)\s*pricing|budget\s+and\s+fees|"
        r"\bbudget\b|\bpricing\b|\bfees?\b"
        r")\b",
        title or "",
        re.IGNORECASE,
    ):
        return False
    # Scored narrative tabs — never skip even when intelligence tagged section-1/2/3.
    if re.search(
        r"\b("
        r"background\s+and\s+experience|qualifications?\s+and\s+experience|"
        r"firm\s+(?:qualifications?|experience|background)|"
        r"agency\s+(?:qualifications?|experience|background)|"
        r"experience\s+(?:and|&)\s+qualifications?"
        r")\b",
        title or "",
        re.IGNORECASE,
    ):
        return False
    dup = (duplicate_of_static_section or "").strip().casefold()
    if dup in {"section-1", "section-2", "section-3", "1", "2", "3"}:
        if re.search(
            r"\b(sample\s+work|portfolio|minimum\s+two|recent\s+campaign|"
            r"agency\s+requirements?|capability\s+matrix)\b",
            title or "",
            re.IGNORECASE,
        ):
            return is_duplicate_static_rfp_section(
                title, static_section_text=static_section_text
            )
        return True
    return is_duplicate_static_rfp_section(title, static_section_text=static_section_text)
