"""Decide how much each unfilled submission item actually matters to THIS RFP.

Replaces `_rfp_mandates_placeholder_ask` on this path. That function asked nine
hardcoded regex questions (FEIN, insurance, e-verify, affidavit, bond, W-9, references,
percent-time, NTE) and, for anything else, fell through to "do at least two tokens of
length >= 4 appear in the RFP text" — so every topic nobody thought to hardcode was
judged by word counting, while deciding what got stripped from real proposals.

Here an agent reads the RFP and must quote it. The quote is then verified by string
containment: mechanical, unable to go stale, and the reason "disqualifying" cannot be
claimed for free. Urgency is a factual claim about the RFP and needs a source like any
other.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.models.proposal import ManualFillFlag

logger = logging.getLogger(__name__)

Criticality = Literal["disqualifying", "scored", "optional"]

_VALID: frozenset[str] = frozenset({"disqualifying", "scored", "optional"})

# Every item is worth listing unless the RFP says otherwise, so an unclassified or
# failed item lands here rather than being dropped or escalated.
_DEFAULT: Criticality = "scored"

# Enough of a clause to identify it; long enough that a coincidental match is implausible.
_MIN_QUOTE_CHARS = 12


def _normalise(text: str) -> str:
    """Collapse whitespace and case so re-wrapped quotes still verify.

    Models re-wrap and re-case text they copy. That is not fabrication, and failing a
    real citation over a line break would push honest items down to "scored".
    """
    return " ".join((text or "").split()).casefold()


def quote_appears_in_rfp(quote: str, rfp_text: str) -> bool:
    """True when `quote` really occurs in `rfp_text`.

    The only mechanical check in the triage path: verification that a quote exists, not
    interpretation of what it means. With no RFP text there is nothing to verify against,
    so nothing can be confirmed.
    """
    needle = _normalise(quote)
    if len(needle) < _MIN_QUOTE_CHARS:
        return False
    haystack = _normalise(rfp_text)
    if not haystack:
        return False
    return needle in haystack


async def _classify_flags(
    *,
    flags: list[ManualFillFlag],
    rfp_text: str,
    rfp_client: str,
    rfp_title: str,
) -> list[dict[str, Any]]:
    """Ask the triage agent to classify each flag. Returns raw, unverified verdicts."""
    from app.services.proposal_langchain_agents import AgentRole, run_json_agent

    lines = [
        f"- {f.tag} (section: {f.section_title or f.section_id})" for f in flags[:60]
    ]
    user_content = (
        f"Client: {rfp_client}\nRFP: {rfp_title}\n\n"
        "Items needing triage:\n" + "\n".join(lines) + "\n\n"
        f"RFP TEXT (the only authority for what is required):\n{(rfp_text or '')[:40_000]}"
    )
    raw, _provider = await run_json_agent(AgentRole.MANUAL_FILL_TRIAGE, user_content)
    items = raw.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _verdict_for(
    flag: ManualFillFlag, verdicts: list[dict[str, Any]], used: set[int]
) -> dict[str, Any] | None:
    """Match a verdict to a flag by tag, each verdict consumable once.

    Tag matching rather than positional: an agent that drops or reorders an item would
    otherwise shift every later flag onto the wrong verdict, which is worse than leaving
    one unclassified.
    """
    target = _normalise(flag.tag)
    for idx, verdict in enumerate(verdicts):
        if idx in used:
            continue
        if _normalise(str(verdict.get("tag", ""))) == target:
            used.add(idx)
            return verdict
    return None


def _apply_verdict(
    flag: ManualFillFlag, verdict: dict[str, Any] | None, rfp_text: str
) -> ManualFillFlag:
    updated = flag.model_copy()
    if verdict is None:
        updated.criticality = _DEFAULT
        return updated

    claimed = str(verdict.get("criticality", "")).strip().casefold()
    criticality: Criticality = claimed if claimed in _VALID else _DEFAULT  # type: ignore[assignment]

    evidence = str(verdict.get("rfpEvidence") or "").strip()
    verified = quote_appears_in_rfp(evidence, rfp_text)

    # A DQ claim without a citation that checks out is an assertion, not a finding.
    # Without this, anything can be escalated by asserting it and the ranking is worthless.
    if criticality == "disqualifying" and not verified:
        logger.info(
            "manual_fill_triage downgrading disqualifying->scored tag=%r "
            "(evidence %s)",
            flag.tag,
            "not found in RFP" if evidence else "absent",
        )
        criticality = "scored"

    updated.criticality = criticality
    updated.rfp_evidence = evidence if verified else None
    updated.why_required = str(verdict.get("whyRequired") or "").strip() or None
    updated.if_skipped = str(verdict.get("ifSkipped") or "").strip() or None
    return updated


async def triage_manual_fill_flags(
    *,
    flags: list[ManualFillFlag],
    rfp_text: str,
    rfp_client: str = "",
    rfp_title: str = "",
) -> list[ManualFillFlag]:
    """Label each flag with criticality and its RFP justification.

    Best-effort: a triage failure marks everything "scored" rather than dropping items.
    Losing a submission gap is far worse than over-reporting one.
    """
    if not flags:
        return []

    try:
        verdicts = await _classify_flags(
            flags=flags,
            rfp_text=rfp_text,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "manual_fill_triage failed, defaulting %d flag(s) to %s: %s",
            len(flags),
            _DEFAULT,
            exc,
        )
        verdicts = []

    used: set[int] = set()
    out = [_apply_verdict(f, _verdict_for(f, verdicts, used), rfp_text) for f in flags]

    counts: dict[str, int] = {}
    for f in out:
        counts[f.criticality or _DEFAULT] = counts.get(f.criticality or _DEFAULT, 0) + 1
    logger.info("manual_fill_triage classified %d flag(s): %s", len(out), counts)
    return out
