"""Shared PricingInstrument + DeliveryConstraints prompt block for all agents.

Prefer typed models on research when present; otherwise fall back to the
Phase-2 opportunity text pack (`format_opportunity_hard_constraints`).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.models.delivery_constraints import DeliveryConstraints
from app.models.pricing_instrument import PricingInstrument

logger = logging.getLogger(__name__)

Focus = Literal["all", "budget", "sow", "timeline"]


_BUYER_FORM_MIN_CONFIDENCE = 0.55


def is_buyer_pricing_form_instrument(
    obj: Any = None,
    *,
    instrument: PricingInstrument | dict | None = None,
) -> bool:
    """True when typed instrument is a renderable buyer Pricing Form.

    Requires ``kind == buyer_pricing_form`` and confidence ≥ 0.55 so low-confidence
    extracts do not suppress Fee Detail / seed buyer-form editor guards.
    """
    inst, _ = _extract_typed(obj, instrument=instrument)
    return (
        inst is not None
        and inst.kind == "buyer_pricing_form"
        and float(inst.confidence or 0) >= _BUYER_FORM_MIN_CONFIDENCE
    )


def _coerce_instrument(raw: Any) -> PricingInstrument | None:
    if raw is None:
        return None
    if isinstance(raw, PricingInstrument):
        return raw
    if isinstance(raw, dict):
        try:
            return PricingInstrument.model_validate(raw)
        except Exception:  # noqa: BLE001
            logger.debug("pricing_delivery_context: instrument coerce failed", exc_info=True)
            return None
    return None


def _coerce_delivery(raw: Any) -> DeliveryConstraints | None:
    if raw is None:
        return None
    if isinstance(raw, DeliveryConstraints):
        return raw
    if isinstance(raw, dict):
        try:
            return DeliveryConstraints.model_validate(raw)
        except Exception:  # noqa: BLE001
            logger.debug("pricing_delivery_context: delivery coerce failed", exc_info=True)
            return None
    return None


def _extract_typed(
    obj: Any,
    *,
    instrument: Any = None,
    delivery: Any = None,
) -> tuple[PricingInstrument | None, DeliveryConstraints | None]:
    inst = _coerce_instrument(instrument)
    dc = _coerce_delivery(delivery)
    if inst is not None and dc is not None:
        return inst, dc

    if obj is None:
        return inst, dc

    if hasattr(obj, "pricing_instrument") or hasattr(obj, "delivery_constraints"):
        if inst is None:
            inst = _coerce_instrument(getattr(obj, "pricing_instrument", None))
        if dc is None:
            dc = _coerce_delivery(getattr(obj, "delivery_constraints", None))
        return inst, dc

    if isinstance(obj, dict):
        if inst is None:
            inst = _coerce_instrument(
                obj.get("pricingInstrument") or obj.get("pricing_instrument")
            )
        if dc is None:
            dc = _coerce_delivery(
                obj.get("deliveryConstraints") or obj.get("delivery_constraints")
            )
        # Nested research-shaped wrapper
        nested = obj.get("research") if isinstance(obj.get("research"), dict) else None
        if nested and (inst is None or dc is None):
            if inst is None:
                inst = _coerce_instrument(
                    nested.get("pricingInstrument") or nested.get("pricing_instrument")
                )
            if dc is None:
                dc = _coerce_delivery(
                    nested.get("deliveryConstraints")
                    or nested.get("delivery_constraints")
                )
    return inst, dc


def _has_typed_signal(
    instrument: PricingInstrument | None,
    delivery: DeliveryConstraints | None,
) -> bool:
    if instrument is not None and instrument.kind != "none":
        return True
    if delivery is None:
        return False
    return bool(
        delivery.tracks
        or delivery.mandatory_deliverables
        or delivery.out_of_scope
        or delivery.bidder_proposes
        or delivery.buyer_owns_deliverables
        or delivery.letter_proposal_gate
        or (
            delivery.horizon
            and any(
                (
                    delivery.horizon.base_term,
                    delivery.horizon.renewals,
                    delivery.horizon.max_term,
                )
            )
        )
    )


def _fmt_nte(value: float | None) -> str:
    if value is None:
        return "NTE TBD"
    if float(value).is_integer():
        return f"${int(value):,}"
    return f"${value:,.2f}"


def format_pricing_delivery_constraints_block(
    obj: Any = None,
    *,
    instrument: PricingInstrument | dict | None = None,
    delivery: DeliveryConstraints | dict | None = None,
    focus: Focus = "all",
    max_chars: int = 8_000,
) -> str:
    """Canonical HARD CONSTRAINTS block for Cost / SOW / Timeline / editor agents."""
    inst, dc = _extract_typed(obj, instrument=instrument, delivery=delivery)
    if not _has_typed_signal(inst, dc):
        from app.services.proposal_opportunity_constraints import (
            format_opportunity_hard_constraints,
        )

        return format_opportunity_hard_constraints(obj, focus=focus, max_chars=max_chars)

    want_budget = focus in {"all", "budget"}
    want_timeline = focus in {"all", "timeline", "budget"}
    # Budget focus keeps track caps but skips long SOW / out-of-scope lists.
    want_sow_lists = focus in {"all", "sow"}

    lines: list[str] = [
        "=== PRICING + DELIVERY CONSTRAINTS (canonical) ===",
        "Treat as binding. Do NOT invent alternate instruments, merge tracks, "
        "or substitute Pricing Guide default scopes when THIS pack conflicts.",
    ]

    if inst is not None:
        lines.append(f"- instrument.kind: {inst.kind}")
        if inst.bid_number:
            lines.append(f"- Bid number: {inst.bid_number}")
        if want_budget and inst.identity_fields:
            labels = ", ".join(f.label for f in inst.identity_fields if f.label)
            if labels:
                lines.append(f"- Form fields: {labels}")
        tracks = inst.tracks or (dc.tracks if dc else [])
        if tracks:
            lines.append("- Tracks / NTEs (do not commingle):")
            for t in tracks:
                label = getattr(t, "label", "") or getattr(t, "id", "")
                nte = getattr(t, "nte_annual", None)
                bits = [str(label).strip() or "track"]
                bits.append(_fmt_nte(nte) + "/yr NTE" if nte is not None else "NTE TBD")
                asks_hourly = getattr(t, "asks_hourly", None)
                asks_hours = getattr(t, "asks_hours", None)
                billing = getattr(t, "billing", "") or ""
                if asks_hourly:
                    bits.append("hourly")
                if asks_hours:
                    bits.append("hours required")
                if billing and not asks_hourly:
                    bits.append(str(billing))
                lines.append(f"  • {' — '.join(bits)}")
    elif dc is not None and dc.tracks:
        lines.append("- Tracks / NTEs (do not commingle):")
        for t in dc.tracks:
            nte_s = _fmt_nte(t.nte_annual) + "/yr NTE" if t.nte_annual is not None else "NTE TBD"
            extra = f" — {t.billing}" if t.billing else ""
            lines.append(f"  • {t.label or t.id} — {nte_s}{extra}")

    if dc is not None:
        if dc.non_commingle_tracks:
            lines.append("- Tracks must not be commingled (separate NTEs / scopes).")
        if want_sow_lists and dc.mandatory_deliverables:
            lines.append(
                "- SOW / Scope of Services musts (Approach must confirm EACH line — "
                "do not drop trailing items):"
            )
            for i, d in enumerate(dc.mandatory_deliverables[:40], 1):
                text = str(d).strip()[:350]
                if text:
                    lines.append(f"  {i}. {text}")
        if want_sow_lists and dc.bidder_proposes:
            lines.append("- Bidder proposes:")
            for item in dc.bidder_proposes[:8]:
                text = str(item).strip()[:300]
                if text:
                    lines.append(f"  • {text}")
        if want_sow_lists and dc.out_of_scope:
            lines.append("- Out of scope (do not invent):")
            for item in dc.out_of_scope[:10]:
                text = str(item).strip()[:300]
                if text:
                    lines.append(f"  • {text}")
        # Ownership + Letter Proposal gate are short binding facts — always surface
        # when set (not only sow focus), so Cost/Timeline/Approach stay consistent.
        if dc.buyer_owns_deliverables:
            lines.append(
                "- Ownership: buyer owns final deliverables / work product "
                "(exclusive). Do NOT claim shared 'usage rights' or agency retention "
                "of ownership unless THIS RFP explicitly allows it."
            )
        if dc.letter_proposal_gate:
            lines.append(
                "- Letter Proposal / task-order gate: no work starts until written "
                "authorization for that assignment. Prefer onboarding timeline over "
                "filler 'per assignment' schedule rows."
            )
        if want_timeline and dc.horizon:
            h = dc.horizon
            if any((h.base_term, h.renewals, h.max_term)):
                lines.append("- Horizon:")
                if h.base_term:
                    lines.append(f"  • Base term: {h.base_term}")
                if h.renewals:
                    lines.append(f"  • Renewals: {h.renewals}")
                if h.max_term:
                    lines.append(f"  • Max term: {h.max_term}")

    # Edit policy — always present so Cost / SOW / Timeline / editors share one rule.
    lines.append("- Cost body: deterministic render only — do not rewrite")
    lines.append("- SOW/Timeline: may polish prose; must not change locked facts")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n…(truncated)"
    logger.info(
        "pricing_delivery_constraints focus=%s kind=%s chars=%s",
        focus,
        getattr(inst, "kind", None),
        len(text),
    )
    return text
