"""Deterministic Cost markdown from a typed PricingInstrument.

Cap gate policy (apply_track_cap_gate): when rate, hours, and nte_annual are all
set and rate * hours > nte_annual + 0.01, clear hours to None. Cleared hours is
the note flag that the track exceeded its NTE — do not invent a lower hours
figure. Rate gate / approved $/hr is out of scope; null rates stay MANUAL FILL.
"""

from __future__ import annotations

import logging

from app.models.pricing_instrument import PricingInstrument, PricingTrack
from app.services.agency_facts import AGENCY_DBA, AGENCY_LEGAL_NAME

logger = logging.getLogger(__name__)

# Form contact for instrument fills — not AGENCY_EMAIL (sonja@) until companyfacts verifies.
_FORM_CONTACT_EMAIL = "connect@zo.agency"
_CONTACT_PERSON = "Sonja Anderson"
_CONTACT_TITLE = "Agency Director / CEO"

_INTERNAL_NOTE_RULES: dict[str, str] = {
    "one_hourly_per_track": "One hourly rate per track/part.",
    "do_not_commingle_ntes": "Do not commingle track NTEs into a single ceiling.",
}


def _usd(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 0.01:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def apply_track_cap_gate(instrument: PricingInstrument) -> PricingInstrument:
    """Clear hours when rate × hours exceeds the track NTE (see module docstring)."""
    if not instrument.tracks:
        return instrument
    new_tracks: list[PricingTrack] = []
    cleared = 0
    for track in instrument.tracks:
        rate = track.rate
        hours = track.hours
        nte = track.nte_annual
        if (
            rate is not None
            and hours is not None
            and nte is not None
            and float(rate) * float(hours) > float(nte) + 0.01
        ):
            new_tracks.append(track.model_copy(update={"hours": None}))
            cleared += 1
        else:
            new_tracks.append(track)
    if cleared:
        logger.info(
            "pricing_instrument_cap_gate cleared_hours=%s tracks=%s kind=%s",
            cleared,
            len(instrument.tracks),
            instrument.kind,
        )
    return instrument.model_copy(update={"tracks": new_tracks})


def _fill_identity_value(key: str, instrument: PricingInstrument) -> str:
    k = (key or "").casefold().strip()
    if k in {"company_name", "company", "legal_name"}:
        return f"{AGENCY_LEGAL_NAME} DBA {AGENCY_DBA}"
    if k in {"contact_person", "contact_name", "authorized_representative"}:
        return _CONTACT_PERSON
    if k in {"contact_email", "email"}:
        return _FORM_CONTACT_EMAIL
    if k in {"contact_title", "title"}:
        return _CONTACT_TITLE
    if k in {"bid_number", "bid"}:
        return (instrument.bid_number or "").strip() or (
            "[MANUAL FILL: SONJA — bid number]"
        )
    return "[MANUAL FILL: SONJA]"


def _rate_cell(track: PricingTrack) -> str:
    if track.rate is not None:
        return _usd(float(track.rate))
    return "[MANUAL FILL: SONJA]"


def _hours_cell(track: PricingTrack) -> str:
    if track.hours is not None:
        h = float(track.hours)
        if abs(h - round(h)) < 0.01:
            return f"{h:,.0f}"
        return f"{h:,.2f}"
    return "[MANUAL FILL: SONJA]"


def _track_row(track: PricingTrack) -> str:
    """Respect asksHourly / asksHours — omit cells the form does not request."""
    nte = track.nte_annual
    nte_s = _usd(nte) if nte is not None else "—"
    prefix = f"**{track.label}** (not to exceed {nte_s}/yr)"
    wants_rate = bool(track.asks_hourly)
    wants_hours = bool(track.asks_hours)
    if wants_rate and wants_hours:
        return f"{prefix} | {_rate_cell(track)} × {_hours_cell(track)} = [≤ {nte_s}]"
    if wants_rate and not wants_hours:
        return f"{prefix} | Hourly rate: {_rate_cell(track)}"
    if wants_hours and not wants_rate:
        return f"{prefix} | Hours: {_hours_cell(track)}"
    return f"{prefix} | [MANUAL FILL: SONJA]"


def _signature_block(instrument: PricingInstrument) -> list[str]:
    sig = instrument.signature
    if sig is None:
        return []
    lines: list[str] = ["### Signature", ""]
    if sig.printed_name_required:
        lines.append(f"| Printed Name | {_CONTACT_PERSON} |")
    if sig.title_required:
        lines.append(f"| Title | {_CONTACT_TITLE} |")
    if sig.signature_required:
        lines.append("| Signature | [SIGN] |")
    if sig.date_required:
        lines.append("| Date | [DATE] |")
    if len(lines) <= 2:
        return []
    # Wrap as a markdown table when any rows exist
    table = [
        "| Field | Response |",
        "| --- | --- |",
        *lines[2:],
        "",
    ]
    return ["### Signature", "", *table]


def _internal_note(instrument: PricingInstrument) -> list[str]:
    sentences: list[str] = []
    for rule_id in instrument.internal_note_rules or []:
        text = _INTERNAL_NOTE_RULES.get(rule_id)
        if text:
            sentences.append(text)
        else:
            logger.debug("pricing_instrument_unknown_note_rule rule=%s", rule_id)
    if not sentences:
        return []
    return [
        f"*Internal note (do not paste onto the official form):* {' '.join(sentences)}",
        "",
    ]


# Align with extract demotion — low-confidence buyer forms must not render.
_BUYER_FORM_MIN_CONFIDENCE = 0.55


def render_pricing_instrument_markdown(instrument: PricingInstrument) -> str:
    """Render buyer Pricing Form markdown from a typed instrument.

    Only ``kind == buyer_pricing_form`` with confidence ≥ 0.55 emits content.
    Other kinds / low confidence return empty so callers keep Fee Detail /
    personnel / legacy worksheet paths.
    Never invents FEIN/phone/fax unless listed in ``identity_fields``.
    Ignores ``budget.form_rate_notes`` — notes come only from ``internal_note_rules``.
    """
    if instrument.kind != "buyer_pricing_form":
        return ""
    if float(instrument.confidence or 0) < _BUYER_FORM_MIN_CONFIDENCE:
        logger.info(
            "pricing_instrument_render skipped low confidence=%.2f kind=%s",
            float(instrument.confidence or 0),
            instrument.kind,
        )
        return ""

    lines: list[str] = [
        "## Buyer Pricing Form",
        "",
        "| Field | Response |",
        "| --- | --- |",
    ]

    bid = (instrument.bid_number or "").strip()
    lines.append(
        f"| BID NUMBER | {bid or '[MANUAL FILL: SONJA — bid number]'} |"
    )

    for field in instrument.identity_fields or []:
        value = _fill_identity_value(field.key, instrument)
        label = (field.label or field.key or "").strip() or field.key
        lines.append(f"| {label} | {value} |")

    lines.append("")
    lines.append("### Pricing tracks")
    lines.append("")
    for track in instrument.tracks or []:
        lines.append(_track_row(track))
        lines.append("")

    lines.extend(_signature_block(instrument))
    lines.extend(_internal_note(instrument))
    return "\n".join(lines)
