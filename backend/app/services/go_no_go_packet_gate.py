"""Decide whether an upload is a scoreable RFP before Go/No-Go spends tokens.

A solicitation notice or an incomplete packet (email us for the full RFP)
must not enter requirement planning, KB retrieval, or scoring. A complete
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
)

logger = logging.getLogger(__name__)

_PACKET_CLASSIFIER_PROMPT = """You read an uploaded procurement document before a marketing agency spends a Go/No-Go analysis on it.

Return JSON only:
{
  "documentKind": "complete_rfp" | "solicitation_notice" | "incomplete_packet" | "other",
  "blocksScoring": true | false,
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
- A solicitation notice, advertisement, or cover page that announces an opportunity and tells vendors how to obtain the real packet, but does not itself include the scope, proposal contents, and selection method.
- An upload that calls itself an RFP yet explicitly requires the reader to email, call, or download the complete RFP, scope, specifications, or proposal requirements, and those materials are not in this upload.
- Any other document that is not the RFP body (questions-only, a single form, an agenda, an addendum with no scope).

When blocksScoring is true, whatItIs must say what the document is, what it does not contain, and what that means for scoring. Name the issuing body when the text does. missing lists the absent materials (scope, proposal structure, evaluation method, pricing instructions) only when they are actually absent. nextStep is how to obtain the real packet, copied from the document. If the document does not say how, say that the upload does not explain how to obtain the rest.

blocksScoring=false when the upload already contains the scope, proposal contents, and selection method. Still fill buyerDemands when that complete RFP explicitly requires the offeror to obtain something from the buyer that is essential to submit a responsive or winning proposal: the remainder of the packet, a missing exhibit, a mandatory registration, a required pre-proposal conference, a pricing workbook, or an addendum that must be in hand. Optional question windows ("questions may be sent to") are not demands unless the document says skipping them withholds materials or makes the proposal non-responsive.

When the demand is "email us to receive the complete RFP" and the complete RFP is not in this upload, that is incomplete_packet with blocksScoring=true, and the email request is also a buyerDemand.

Every email, phone, URL, and deadline you output must appear in the document. evidenceQuote and each demand quote must be copied from the document, not paraphrased. Do not invent a contact. Leave deadlineQuote empty when no deadline is stated.
"""

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_KINDS = frozenset(
    {"complete_rfp", "solicitation_notice", "incomplete_packet", "other"}
)


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
    blocks = wants_block and kind != "complete_rfp" and evidence_ok
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
    if blocks and not what:
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

    excerpt = body[:24_000]
    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": _PACKET_CLASSIFIER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Document title: {title or '(not provided)'}\n\n"
                        f"Uploaded text:\n{excerpt}"
                    ),
                },
            ],
            max_tokens=900,
            temperature=0.0,
            tier="light",
            node_name="go_no_go_packet_gate",
            rfp_id=rfp_id,
        )
        parsed = parse_packet_read(raw if isinstance(raw, dict) else {}, rfp_text=excerpt)
        logger.info(
            "packet gate for %s via %s: kind=%s blocks=%s demands=%d",
            rfp_id or "unknown",
            provider,
            parsed.document_kind,
            parsed.blocks_scoring,
            len(parsed.buyer_demands),
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
