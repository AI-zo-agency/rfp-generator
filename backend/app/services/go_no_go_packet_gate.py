"""Decide whether an upload is a scoreable RFP before Go/No-Go spends tokens.

A solicitation notice, an RFI, an RFQ, or an incomplete packet (email us
for the full RFP) must not enter requirement planning, KB retrieval, or
scoring. A complete
RFP that still requires the offeror to obtain something essential from the
buyer is scored, and those demands are returned for a banner above the result.

Judgment is one light LLM call. Contacts, deadlines, and demand quotes are
kept only when they appear in the uploaded text.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.go_no_go import (
    GoNoGoAnalysis,
    GoNoGoDeadlineInfo,
    GoNoGoDimension,
    GoNoGoFlag,
    RfpBuyerDemand,
    RfpPacketRead,
    UnresolvedReference,
)

logger = logging.getLogger(__name__)

_PACKET_CLASSIFIER_PROMPT = """You read an uploaded procurement document before a marketing agency spends a Go/No-Go analysis on it.

Return JSON only:
{
  "documentKind": "complete_rfp" | "solicitation_notice" | "incomplete_packet" | "rfi" | "rfq" | "other",
  "blocksScoring": true | false,
  "unresolvedReferences": [
    {
      "target": "the name of the cited part, copied from the quote",
      "quote": "verbatim sentence in this upload that points at that part"
    }
  ],
  "headline": "one line a proposal manager sees first",
  "whatItIs": "thorough plain-language explanation of what this upload actually is",
  "missing": ["materials absent from this upload that a proposer needs"],
  "nextStep": "the single next action, using only contacts that appear in the document",
  "deadlineQuote": "verbatim deadline sentence from the document, or empty",
  "evidenceQuote": "verbatim sentence that shows why this is or is not a complete RFP",
  "buyerDemands": [
    {
      "action": "what the offeror must do",
      "contact": "email, phone, or URL copied from the document, or empty",
      "whyEssential": "why a responsive proposal cannot be prepared without this",
      "quote": "verbatim sentence that states the demand"
    }
  ]
}

Judge by meaning. Do not use keyword checklists.

A complete RFP is scoreable: it contains enough for a proposer to understand the work, what the proposal must include, and how the buyer will choose. A price form that says "propose your price" without a ceiling is still complete.

blocksScoring=true when the upload is not that packet:
- rfi: a Request for Information. The buyer is collecting information, capabilities, qualifications, or market input. It is not asking for a competitive proposal that will be evaluated for contract award. Say so in the headline ("This is an RFI, not an RFP") and in whatItIs: what the buyer is asking for, that this is not a proposal competition, and the response the document actually requests. If the text says an RFP will follow, include that as nextStep. Do not invent a later RFP.
- rfq: a Request for Quotation or Request for Quote. The buyer wants a price quotation. It is not an RFP, even when a short description of the work is included. Headline must say this is an RFQ, not an RFP. whatItIs must explain that the response is a quote, not a scored proposal, and restate any submission instructions that appear in the text.
- A solicitation notice, advertisement, or cover page that announces an opportunity and tells vendors how to obtain the real packet, but does not itself include the scope, proposal contents, and selection method.
- An upload that calls itself an RFP yet explicitly requires the reader to email, call, or download the complete RFP, scope, specifications, or proposal requirements, and those materials are not in this upload.
- Any other document that is not the RFP body (questions-only, a single form, an agenda, an addendum with no scope).

Do not label a true proposal competition as rfi or rfq just because it asks questions or asks for a price. Use rfi or rfq when the document's purpose is information-gathering or a quotation.

When blocksScoring is true, whatItIs must say what the document is, what it does not contain, and what that means for scoring. Name the issuing body when the text does. missing lists the absent materials (scope, proposal structure, evaluation method, pricing instructions) only when they are actually absent. For an RFI or RFQ, missing may be empty when the document is complete for what it is — the reason to stop is that it is not an RFP. nextStep is how to respond or how to obtain the real packet, copied from the document. If the document does not say how, say that the upload does not explain the next action.

blocksScoring=false when the upload already contains the scope, proposal contents, and selection method. Still fill buyerDemands when that complete RFP explicitly requires the offeror to obtain something from the buyer that is essential to submit a responsive or winning proposal: the remainder of the packet, a missing exhibit, a mandatory registration, a required pre-proposal conference, a pricing workbook, or an addendum that must be in hand. Optional question windows ("questions may be sent to") are not demands unless the document says skipping them withholds materials or makes the proposal non-responsive.

When the demand is "email us to receive the complete RFP" and the complete RFP is not in this upload, that is incomplete_packet with blocksScoring=true, and the email request is also a buyerDemand.

An unresolved reference is different from a buyer demand. It is a sentence in this upload that points at another part of the same document — a section, exhibit, attachment, appendix, or packet section — and the body of that part is not in the upload. The text does not tell the offeror to email, call, download, or register. Cite the sentence verbatim as quote, and copy the name of the missing part into target from that sentence. One entry per missing body. Omit a reference whose body appears anywhere in the upload. A complete RFP can still have unresolved references; do not set blocksScoring just because one cited part is missing.

Every email, phone, URL, and deadline you output must appear in the document. evidenceQuote and each demand quote must be copied from the document, not paraphrased. Do not invent a contact. Leave deadlineQuote empty when no deadline is stated.
"""

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_KINDS = frozenset(
    {
        "complete_rfp",
        "solicitation_notice",
        "incomplete_packet",
        "rfi",
        "rfq",
        "other",
    }
)
# These instruments are never scored as an RFP, even if the model forgets blocksScoring.
_NON_RFP_INSTRUMENTS = frozenset({"rfi", "rfq"})
_INSTRUMENT_HEADLINE = {
    "rfi": "This upload is an RFI, not an RFP",
    "rfq": "This upload is an RFQ, not an RFP",
}
_INSTRUMENT_WHAT = {
    "rfi": (
        "This upload is a Request for Information. The buyer is asking for "
        "information, not a competitive proposal. Go/No-Go scoring does not apply."
    ),
    "rfq": (
        "This upload is a Request for Quotation. The buyer is asking for a quote, "
        "not a proposal scored as an RFP. Go/No-Go scoring does not apply."
    ),
}


def default_packet_read() -> RfpPacketRead:
    """Fail open: an unread upload is treated as scoreable."""
    return RfpPacketRead(document_kind="complete_rfp", blocks_scoring=False)


def _normalize(text: str) -> str:
    """Whitespace- and punctuation-insensitive compare for verbatim grounding."""
    collapsed = " ".join((text or "").casefold().split())
    return re.sub(r"[^a-z0-9@.+]+", " ", collapsed).strip()


def _grounded(quote: str, source: str) -> bool:
    snippet = _normalize(quote)
    if len(snippet) < 12:
        return False
    return snippet in _normalize(source)


def _strip_invented_contacts(text: str, source: str) -> str:
    source_norm = _normalize(source)

    def keep_email(match: re.Match[str]) -> str:
        email = match.group(0)
        if email.casefold() in source.casefold():
            return email
        return ""

    def keep_url(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,;")
        if _normalize(url) in source_norm:
            return match.group(0)
        return ""

    cleaned = _EMAIL_RE.sub(keep_email, text or "")
    cleaned = _URL_RE.sub(keep_url, cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" \t,;")


def _clip(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


# Words that name a separately bound part of a solicitation. "Exhibit C",
# "Attachment A", "Appendix 2" — the family word plus a short identifier.
_PART_FAMILIES = frozenset(
    {
        "attachment",
        "exhibit",
        "appendix",
        "schedule",
        "addendum",
        "annex",
        "enclosure",
        "form",
        "section",
    }
)
# Sections are almost always in the upload; only bound parts get the TOC backstop.
_LISTED_PART_FAMILIES = _PART_FAMILIES - {"section"}
_PART_ID_STOPWORDS = frozenset({"OF", "TO", "IN", "ON", "AT", "BY", "OR", "AN", "AS"})
_LIST_MARKS = " \t•·▪◦*-–—()."


def _strip_list_marker(line: str) -> str:
    """Drop bullets and numbering: '• Exhibit C', '1. Attachment A', 'o Form'."""
    text = line.strip().lstrip(_LIST_MARKS + "0123456789")
    head, _, rest = text.partition(" ")
    if head in {"o", "a.", "b.", "c.", "d."} and rest:
        text = rest
    return text.strip()


def _part_key(text: str) -> str:
    """'Exhibit C: Scope of Work' → 'exhibit c'; '' when it names no bound part."""
    words = _strip_list_marker(text).split()
    if len(words) < 2 or words[0].casefold() not in _PART_FAMILIES:
        return ""
    ident = words[1].strip(":.,;–—-()")
    if not ident or len(ident) > 5:
        return ""
    has_digit = any(ch.isdigit() for ch in ident)
    if not has_digit and not (ident.isupper() and ident not in _PART_ID_STOPWORDS):
        return ""
    return f"{words[0].casefold()} {ident.casefold()}"


def _is_list_entry(lines: list[str], index: int) -> bool:
    """A TOC or submission-list line: dotted leaders, or a neighbour naming another part."""
    if "...." in lines[index]:
        return True
    for step in (-1, 1):
        j = index + step
        while 0 <= j < len(lines) and not lines[j].strip():
            j += step
        if 0 <= j < len(lines) and _part_key(lines[j]):
            return True
    return False


def _starts_unit(prefix: str) -> bool:
    """True when the text before a mention leaves it standing as a heading."""
    rest = prefix.strip().strip(_LIST_MARKS + "0123456789")
    if len(rest) <= 1:
        return True
    # In-line heading after a finished sentence: "…for details. Section 7: …"
    return prefix.rstrip().endswith((".", "!", "?")) and prefix.endswith((" ", "\t"))


def _normalized_body_follows(target: str, quote: str, source: str) -> bool:
    folded_source = _normalize(source)
    folded_quote = _normalize(quote)
    folded_target = _normalize(target)
    if len(folded_target) < 8 or folded_target not in folded_quote:
        return False
    remaining = folded_source.replace(folded_quote, " ")
    idx = remaining.find(folded_target)
    if idx < 0:
        return False
    window = remaining[idx + len(folded_target) : idx + len(folded_target) + 500]
    alnum = sum(ch.isalnum() for ch in window)
    return alnum >= 120


def _body_follows_target(target: str, quote: str, source: str) -> bool:
    """True when the cited part has its own text in the upload.

    A body starts at a heading: the part's name standing at the start of a line
    or right after a finished sentence, not inside a table of contents or a
    submission checklist. Any other mention is a reference. Counting every later
    mention as a body dropped "Exhibit C" and "Exhibit D" on the Minnesota Lottery
    upload, because "is included in Exhibit C." is followed by more RFP text.
    """
    key = _part_key(target)
    if not key:
        return _normalized_body_follows(target, quote, source)
    quote_folded = " ".join((quote or "").casefold().split())
    lines = (source or "").splitlines()
    for index, line in enumerate(lines):
        folded = " ".join(line.casefold().split())
        if quote_folded and quote_folded in folded and len(folded) <= len(quote_folded) + 40:
            continue
        start = folded.find(key)
        while start >= 0:
            end = start + len(key)
            boundary = end >= len(folded) or not folded[end].isalnum()
            if boundary and _starts_unit(folded[:start]) and not _is_list_entry(lines, index):
                after = folded[end:] + " " + " ".join(lines[index + 1 : index + 12])
                if sum(ch.isalnum() for ch in after[:500]) >= 120:
                    return True
            start = folded.find(key, end)
    return False


def _listed_missing_parts(
    source: str, already: list[UnresolvedReference]
) -> list[UnresolvedReference]:
    """Bound parts named in a TOC or checklist line whose body is not in the upload.

    The classifier lists unresolved references by judgment and skipped four of
    nine on the Minnesota Lottery packet. Every part the RFP itself lists is
    checked here so the "cited, not included" list does not depend on the model.
    """
    seen = {_part_key(ref.target) for ref in already}
    found: list[UnresolvedReference] = []
    for raw_line in (source or "").splitlines():
        line = raw_line.strip()
        key = _part_key(line)
        if not key or key in seen or len(line) > 140:
            continue
        if key.split()[0] not in _LISTED_PART_FAMILIES:
            continue
        seen.add(key)
        target = _strip_list_marker(line.split("....")[0]).strip(" .")
        if len(_normalize(target)) < 8 or _body_follows_target(target, line, source):
            continue
        found.append(UnresolvedReference(target=_clip(target, 200), quote=_clip(line, 500)))
    return found


# A full packet names 8+ attachments and exhibits (Minnesota Lottery: 9).
_MAX_UNRESOLVED_REFERENCES = 16


def _parse_unresolved_references(raw: dict[str, Any], source: str) -> list[UnresolvedReference]:
    rows = raw.get("unresolvedReferences") or raw.get("unresolved_references") or []
    if not isinstance(rows, list):
        rows = []
    kept: list[UnresolvedReference] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        target = _clip(str(item.get("target") or ""), 200)
        if not _grounded(quote, source) or len(_normalize(target)) < 8:
            logger.info(
                "unresolved reference dropped ungrounded target=%s",
                target[:80],
            )
            continue
        if _normalize(target) not in _normalize(quote):
            logger.info(
                "unresolved reference dropped target not in quote target=%s",
                target[:80],
            )
            continue
        if _body_follows_target(target, quote, source):
            logger.info(
                "unresolved reference dropped body present target=%s",
                target[:80],
            )
            continue
        kept.append(UnresolvedReference(target=target, quote=_clip(quote, 500)))
        if len(kept) >= _MAX_UNRESOLVED_REFERENCES:
            break
    kept.extend(_listed_missing_parts(source, kept))
    kept = kept[:_MAX_UNRESOLVED_REFERENCES]
    logger.info("unresolved references kept=%s", len(kept))
    return kept


def _parse_demand(raw: Any, source: str) -> RfpBuyerDemand | None:
    if not isinstance(raw, dict):
        return None
    quote = str(raw.get("quote") or "").strip()
    if not _grounded(quote, source):
        return None
    action = _strip_invented_contacts(str(raw.get("action") or ""), source)
    if not action:
        return None
    contact = _strip_invented_contacts(str(raw.get("contact") or ""), source)
    why = _clip(str(raw.get("whyEssential") or raw.get("why_essential") or ""), 400)
    return RfpBuyerDemand(
        action=_clip(action, 400),
        contact=_clip(contact, 200),
        why_essential=why,
        quote=_clip(quote, 500),
    )


def parse_packet_read(raw: dict[str, Any], *, rfp_text: str) -> RfpPacketRead:
    """Parse classifier JSON. Ungrounded blocks fail open so a real RFP still scores."""
    kind_raw = str(raw.get("documentKind") or raw.get("document_kind") or "").strip()
    kind = kind_raw if kind_raw in _KINDS else "complete_rfp"
    evidence = str(raw.get("evidenceQuote") or raw.get("evidence_quote") or "").strip()
    evidence_ok = _grounded(evidence, rfp_text)

    wants_block = bool(raw.get("blocksScoring") if "blocksScoring" in raw else raw.get("blocks_scoring"))
    # An RFI or RFQ is not scored as an RFP once a verbatim quote grounds the label.
    blocks = (
        (wants_block or kind in _NON_RFP_INSTRUMENTS)
        and kind != "complete_rfp"
        and evidence_ok
    )
    if not evidence_ok and kind != "complete_rfp":
        logger.info(
            "packet gate quote not in upload — not blocking scoring (kind=%s)",
            kind,
        )
        kind = "complete_rfp"
        blocks = False

    what = _strip_invented_contacts(str(raw.get("whatItIs") or raw.get("what_it_is") or ""), rfp_text)
    headline = _strip_invented_contacts(str(raw.get("headline") or ""), rfp_text)
    next_step = _strip_invented_contacts(
        str(raw.get("nextStep") or raw.get("next_step") or ""), rfp_text
    )
    if blocks and kind in _NON_RFP_INSTRUMENTS:
        if not headline:
            headline = _INSTRUMENT_HEADLINE[kind]
        if not what:
            what = _INSTRUMENT_WHAT[kind]
    elif blocks and not what:
        blocks = False
        kind = "complete_rfp"

    deadline_quote = str(raw.get("deadlineQuote") or raw.get("deadline_quote") or "").strip()
    deadline_note = deadline_quote if _grounded(deadline_quote, rfp_text) else ""

    missing: list[str] = []
    raw_missing = raw.get("missing")
    if isinstance(raw_missing, list):
        for item in raw_missing:
            if not isinstance(item, str):
                continue
            cleaned = _clip(item, 200)
            if cleaned and cleaned.casefold() not in {m.casefold() for m in missing}:
                missing.append(cleaned)
            if len(missing) >= 8:
                break

    references = _parse_unresolved_references(raw, rfp_text)

    demands: list[RfpBuyerDemand] = []
    raw_demands = raw.get("buyerDemands") or raw.get("buyer_demands") or []
    if isinstance(raw_demands, list):
        for item in raw_demands:
            parsed = _parse_demand(item, rfp_text)
            if parsed is None:
                continue
            demands.append(parsed)
            if len(demands) >= 8:
                break

    if not blocks:
        # A complete RFP keeps only the essential-demand banner, not a fake gap list.
        missing = []
        if kind != "complete_rfp":
            kind = "complete_rfp"

    return RfpPacketRead(
        document_kind=kind,  # type: ignore[arg-type]
        blocks_scoring=blocks,
        headline=_clip(headline, 200),
        what_it_is=_clip(what, 2000),
        missing=missing if blocks else [],
        next_step=_clip(next_step, 800) if blocks or demands else "",
        deadline_note=_clip(deadline_note, 400),
        buyer_demands=demands,
        unresolved_references=references,
    )


async def classify_rfp_packet(
    text: str,
    *,
    rfp_id: str = "",
    title: str = "",
) -> RfpPacketRead:
    """One light call. Empty text and classifier failure do not block scoring."""
    body = (text or "").strip()
    if len(body) < 40:
        return default_packet_read()

    from app.services import llm

    if not llm.is_configured():
        return default_packet_read()

    logger.info(
        "packet gate input rfp_id=%s chars=%s",
        rfp_id or "unknown",
        len(body),
    )
    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": _PACKET_CLASSIFIER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Document title: {title or '(not provided)'}\n\n"
                        f"Uploaded text:\n{body}"
                    ),
                },
            ],
            max_tokens=8192,
            temperature=0.0,
            tier="light",
            node_name="go_no_go_packet_gate",
            rfp_id=rfp_id,
        )
        parsed = parse_packet_read(raw if isinstance(raw, dict) else {}, rfp_text=body)
        logger.info(
            "packet gate for %s via %s: kind=%s blocks=%s demands=%d unresolved=%d",
            rfp_id or "unknown",
            provider,
            parsed.document_kind,
            parsed.blocks_scoring,
            len(parsed.buyer_demands),
            len(parsed.unresolved_references),
        )
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "packet gate failed for %s: %s — scoring will continue",
            rfp_id or "unknown",
            str(exc)[:160],
        )
        return default_packet_read()


def _pending_dimension(message: str) -> GoNoGoDimension:
    return GoNoGoDimension(
        summary=message,
        score_impact="Scoring skipped — this upload is not a complete RFP.",
        flags=[
            GoNoGoFlag(
                category="incomplete_packet",
                severity="critical",
                message=message,
            )
        ],
    )


def build_blocked_packet_analysis(
    packet: RfpPacketRead,
    *,
    deadline: dict[str, Any] | None = None,
) -> GoNoGoAnalysis:
    """Return a saved analysis that explains the upload and does not score it."""
    headline = packet.headline or "This upload is not a complete RFP"
    parts = [packet.what_it_is or headline]
    if packet.missing:
        parts.append("Not in this upload: " + "; ".join(packet.missing) + ".")
    if packet.next_step:
        parts.append("Next step: " + packet.next_step)
    if packet.deadline_note:
        parts.append("Submission deadline stated in the upload: " + packet.deadline_note)
    summary = " ".join(part.strip() for part in parts if part.strip())

    questions: list[str] = []
    if packet.next_step:
        questions.append(packet.next_step)
    if packet.missing:
        questions.append(
            "After you have the complete packet, upload it and run Go/No-Go again. "
            "Still missing from this file: " + "; ".join(packet.missing) + "."
        )

    deadline_model = None
    if deadline:
        try:
            deadline_model = GoNoGoDeadlineInfo.model_validate(deadline)
        except Exception:  # noqa: BLE001
            deadline_model = None

    pending = packet.what_it_is or headline
    return GoNoGoAnalysis(
        fit_score=None,
        worth_score=None,
        recommendation=None,
        insufficient_data=True,
        summary=summary,
        scope_match=_pending_dimension(pending),
        sector_match=_pending_dimension(pending),
        compliance=_pending_dimension(pending),
        team_match=_pending_dimension(pending),
        clarifying_questions=questions,
        critical_gaps=[],
        stage_one_report="",
        deadline=deadline_model,
        provider="packet-gate",
        packet_read=packet.model_copy(update={"headline": headline, "blocks_scoring": True}),
    )
