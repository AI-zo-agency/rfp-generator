"""Extract + normalize PricingInstrument and DeliveryConstraints from RFP text.

LLM extracts with evidence quotes; code validates. No client-specific branches —
DuPage appears only in test fixtures.

On LLM failure: kind=none confidence=0 + empty DeliveryConstraints + warning log.

After LLM JSON: ground fields to excerpt (whitespace-normalized substring);
demote buyer_pricing_form when confidence < 0.55 to kind=none.

Opportunity bootstrap: when LLM returns empty delivery tracks but opportunity
budget intel / delivery ceiling text clearly indicates separate track NTEs
(Part/track labels + NTE language), seed DeliveryConstraints.tracks. If unsure,
leave tracks empty — never invent from any two dollar amounts.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.delivery_constraints import (
    DeliveryConstraints,
    DeliveryHorizon,
    DeliveryTrack,
)
from app.models.pricing_instrument import (
    IdentityField,
    InstrumentEvidence,
    PricingInstrument,
    PricingInstrumentKind,
    PricingSignature,
    PricingTrack,
)
from app.services.proposal_intelligence.agent_base import safe_chat_json
from app.services.proposal_rfp_excerpt import closing_package_excerpt

logger = logging.getLogger(__name__)

AGENT = "pricing_instrument_extract"

_ALLOWED_KINDS: frozenset[str] = frozenset(
    {
        "buyer_pricing_form",
        "personnel_loading",
        "phased_fee_schedule",
        "none",
    }
)

_KIND_TO_BUDGET_FORMAT: dict[str, str] = {
    "buyer_pricing_form": "blended_rate_form",
    "personnel_loading": "personnel_loading",
    "phased_fee_schedule": "phased",
    "none": "phased",
}

# Buyer form below this confidence is demoted to kind=none (renderer also gates).
_BUYER_FORM_MIN_CONFIDENCE = 0.55

_SYSTEM = """You extract THIS RFP's Cost / Pricing INSTRUMENT and delivery constraints.

Return JSON only:
{
  "instrument": {
    "kind": "buyer_pricing_form|personnel_loading|phased_fee_schedule|none",
    "bidNumber": "string or null — ONLY if THIS RFP states a bid/solicitation number on the form",
    "identityFields": [{"key": "snake_case", "label": "EXACT FORM LABEL", "valueSource": "extract"}],
    "tracks": [{
      "id": "slug",
      "label": "exact track/part label from RFP",
      "nteAnnual": number or null,
      "asksHourly": bool,
      "asksHours": bool
    }],
    "signature": {
      "printedNameRequired": bool,
      "titleRequired": bool,
      "signatureRequired": bool,
      "dateRequired": bool
    },
    "confidence": 0.0,
    "evidence": [{"field": "string", "quote": "verbatim RFP quote", "locator": "page/section if known"}],
    "internalNoteRules": ["one_hourly_per_track", "do_not_commingle_ntes"]
  },
  "delivery": {
    "tracks": [{"id": "slug", "label": "string", "nteAnnual": number or null, "billing": "hourly|fixed|other"}],
    "mandatoryDeliverables": ["string"],
    "bidderProposes": ["string"],
    "outOfScope": ["string"],
    "horizon": {"baseTerm": "string", "renewals": "string", "maxTerm": "string"},
    "nonCommingleTracks": bool,
    "evidence": [{"field": "string", "quote": "verbatim quote", "locator": "string"}]
  }
}

Rules:
- kind=buyer_pricing_form ONLY when THIS RFP has a fillable Pricing / Quotation / Proposal Pricing form
  (identity + rate/hours lines and/or Part tracks with NTEs).
- kind=personnel_loading when THIS RFP asks for role/labor-category hourly rate tables.
- kind=phased_fee_schedule when fees are by phase/deliverable with no buyer fillable form.
- kind=none when no cost instrument is found or confidence is low.
- identityFields: ONLY labels that appear on THIS form — never invent FEIN/phone/fax.
- bidNumber: copy ONLY from THIS RFP; never invent or borrow from another solicitation.
- Every non-null bidNumber, NTE, and form field MUST have an evidence quote from THIS excerpt.
- Align delivery.tracks ids/labels with instrument.tracks when both exist.
- Do NOT copy fields, bid numbers, or NTEs from other RFPs or prior knowledge.
- Do NOT invent rates or hours.
"""

# Money amounts in opportunity ceiling/notes — not capability synonym matching.
_MONEY_RE = re.compile(
    r"\$\s*([\d]{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PART_LABEL_RE = re.compile(
    r"(part\s*\d+|track\s*\d+)",
    re.IGNORECASE,
)


def budget_format_for_instrument(instrument: PricingInstrument) -> str:
    """Map instrument.kind → manuscript budgetFormat string."""
    return _KIND_TO_BUDGET_FORMAT.get(instrument.kind, "phased")


def _clamp_kind(raw: Any) -> PricingInstrumentKind:
    text = str(raw or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "buyer_pricing_form": "buyer_pricing_form",
        "buyer_form": "buyer_pricing_form",
        "blended_rate_form": "buyer_pricing_form",
        "pricing_form": "buyer_pricing_form",
        "personnel_loading": "personnel_loading",
        "phased_fee_schedule": "phased_fee_schedule",
        "phased": "phased_fee_schedule",
        "none": "none",
    }
    mapped = aliases.get(text, "none")
    if mapped not in _ALLOWED_KINDS:
        return "none"
    return mapped  # type: ignore[return-value]


def _clamp_confidence(raw: Any) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _parse_money(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_bid_number(raw: Any) -> str | None:
    """Never invent — empty/whitespace → None."""
    text = str(raw or "").strip()
    return text or None


def _normalize_evidence(raw: Any) -> list[InstrumentEvidence]:
    if not isinstance(raw, list):
        return []
    out: list[InstrumentEvidence] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not field or not quote:
            continue
        out.append(
            InstrumentEvidence(
                field=field[:120],
                quote=quote[:600],
                locator=str(item.get("locator") or "")[:120],
            )
        )
    return out


def _normalize_pricing_tracks(raw: Any) -> list[PricingTrack]:
    if not isinstance(raw, list):
        return []
    out: list[PricingTrack] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        tid = str(item.get("id") or "").strip() or f"track-{i + 1}"
        out.append(
            PricingTrack(
                id=tid[:80],
                label=label[:200],
                nteAnnual=_parse_money(item.get("nteAnnual", item.get("nte_annual"))),
                asksHourly=bool(item.get("asksHourly", item.get("asks_hourly", False))),
                asksHours=bool(item.get("asksHours", item.get("asks_hours", False))),
                rate=_parse_money(item.get("rate")),
                hours=_parse_money(item.get("hours")),
            )
        )
    return out


def _normalize_identity_fields(raw: Any) -> list[IdentityField]:
    if not isinstance(raw, list):
        return []
    out: list[IdentityField] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        if not key or not label:
            continue
        src = str(item.get("valueSource") or item.get("value_source") or "extract").strip()
        if src not in {"companyfacts", "extract", "manual"}:
            src = "extract"
        out.append(IdentityField(key=key[:80], label=label[:200], valueSource=src))  # type: ignore[arg-type]
    return out


def _normalize_signature(raw: Any) -> PricingSignature | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return PricingSignature(
        printedNameRequired=bool(
            raw.get("printedNameRequired", raw.get("printed_name_required", False))
        ),
        titleRequired=bool(raw.get("titleRequired", raw.get("title_required", False))),
        signatureRequired=bool(
            raw.get("signatureRequired", raw.get("signature_required", False))
        ),
        dateRequired=bool(raw.get("dateRequired", raw.get("date_required", False))),
    )


def normalize_instrument_payload(raw: Any) -> PricingInstrument:
    """Validate / clamp LLM (or fixture) instrument JSON → PricingInstrument."""
    if isinstance(raw, PricingInstrument):
        return raw
    if not isinstance(raw, dict):
        return PricingInstrument(kind="none", confidence=0.0)
    rules_raw = raw.get("internalNoteRules") or raw.get("internal_note_rules") or []
    rules = (
        [str(r).strip() for r in rules_raw if str(r).strip()]
        if isinstance(rules_raw, list)
        else []
    )
    return PricingInstrument(
        kind=_clamp_kind(raw.get("kind")),
        bidNumber=_clean_bid_number(raw.get("bidNumber", raw.get("bid_number"))),
        identityFields=_normalize_identity_fields(
            raw.get("identityFields") or raw.get("identity_fields")
        ),
        tracks=_normalize_pricing_tracks(raw.get("tracks")),
        signature=_normalize_signature(raw.get("signature")),
        evidence=_normalize_evidence(raw.get("evidence")),
        confidence=_clamp_confidence(raw.get("confidence")),
        internalNoteRules=rules[:20],
    )


def _normalize_delivery_tracks(raw: Any) -> list[DeliveryTrack]:
    if not isinstance(raw, list):
        return []
    out: list[DeliveryTrack] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        tid = str(item.get("id") or "").strip() or f"track-{i + 1}"
        out.append(
            DeliveryTrack(
                id=tid[:80],
                label=label[:200],
                nteAnnual=_parse_money(item.get("nteAnnual", item.get("nte_annual"))),
                billing=str(item.get("billing") or "")[:40],
            )
        )
    return out


def _normalize_str_list(raw: Any, *, limit: int = 40) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            out.append(text[:400])
        if len(out) >= limit:
            break
    return out


def _normalize_horizon(raw: Any) -> DeliveryHorizon | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return DeliveryHorizon(
        baseTerm=str(raw.get("baseTerm") or raw.get("base_term") or "")[:200],
        renewals=str(raw.get("renewals") or "")[:200],
        maxTerm=str(raw.get("maxTerm") or raw.get("max_term") or "")[:200],
    )


def normalize_delivery_payload(raw: Any) -> DeliveryConstraints:
    """Validate / clamp LLM (or fixture) delivery JSON → DeliveryConstraints."""
    if isinstance(raw, DeliveryConstraints):
        return raw
    if not isinstance(raw, dict):
        return DeliveryConstraints()
    return DeliveryConstraints(
        tracks=_normalize_delivery_tracks(raw.get("tracks")),
        mandatoryDeliverables=_normalize_str_list(
            raw.get("mandatoryDeliverables") or raw.get("mandatory_deliverables")
        ),
        bidderProposes=_normalize_str_list(
            raw.get("bidderProposes") or raw.get("bidder_proposes")
        ),
        outOfScope=_normalize_str_list(raw.get("outOfScope") or raw.get("out_of_scope")),
        horizon=_normalize_horizon(raw.get("horizon")),
        nonCommingleTracks=bool(
            raw.get("nonCommingleTracks", raw.get("non_commingle_tracks", False))
        ),
        evidence=_normalize_evidence(raw.get("evidence")),
    )


def _opportunity_dict(opportunity: Any | None) -> dict[str, Any]:
    if opportunity is None:
        return {}
    if hasattr(opportunity, "model_dump"):
        try:
            dumped = opportunity.model_dump(by_alias=True)
            if isinstance(dumped, dict) and (
                "understanding" in dumped or "scope" in dumped or "opportunity" in dumped
            ):
                return dumped
            return dumped if isinstance(dumped, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    if isinstance(opportunity, dict):
        if "understanding" in opportunity or "scope" in opportunity:
            return opportunity
        nested = opportunity.get("opportunity")
        if isinstance(nested, dict):
            return nested
        return opportunity
    return {}


def _planish_dict(opportunity: Any | None) -> dict[str, Any]:
    """Best-effort plan-shaped dict (may include delivery.budget)."""
    if opportunity is None:
        return {}
    if hasattr(opportunity, "model_dump"):
        try:
            dumped = opportunity.model_dump(by_alias=True)
            return dumped if isinstance(dumped, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    if isinstance(opportunity, dict):
        return opportunity
    return {}


def _opportunity_budget_blobs(opportunity: Any | None) -> list[str]:
    """Collect ceiling / notes text from opportunity + delivery.budget for bootstrap."""
    opp = _opportunity_dict(opportunity)
    # If we got a full plan, dig into opportunity key.
    if "opportunity" in opp and isinstance(opp.get("opportunity"), dict):
        if "understanding" not in opp:
            opp = opp["opportunity"]
    blobs: list[str] = []
    und = opp.get("understanding") if isinstance(opp.get("understanding"), dict) else {}
    bi = und.get("budgetIntel") if isinstance(und.get("budgetIntel"), dict) else {}
    if not bi and isinstance(und.get("budget_intel"), dict):
        bi = und["budget_intel"]
    for key in ("ceiling", "notes", "maximumAgreementAmount", "maximum_agreement_amount"):
        val = str(bi.get(key) or "").strip()
        if val:
            blobs.append(val)
    scope = opp.get("scope") if isinstance(opp.get("scope"), dict) else {}
    notes = str(scope.get("notes") or "").strip()
    if notes:
        blobs.append(notes)

    planish = _planish_dict(opportunity)
    delivery = planish.get("delivery") if isinstance(planish.get("delivery"), dict) else {}
    budget = delivery.get("budget") if isinstance(delivery.get("budget"), dict) else {}
    for key in ("ceiling", "constraints", "pricingStrategy", "pricing_strategy"):
        val = budget.get(key)
        if isinstance(val, list):
            blobs.extend(str(c).strip() for c in val if str(c).strip())
        else:
            text = str(val or "").strip()
            if text:
                blobs.append(text)
    return blobs


def _parse_nte_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for m in _MONEY_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if val <= 0:
            continue
        if val not in amounts:
            amounts.append(val)
    return amounts


def _ws_norm(text: str) -> str:
    """Collapse whitespace for quote-in-excerpt substring checks."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _quote_in_excerpt(quote: str, excerpt: str) -> bool:
    q = _ws_norm(quote)
    if not q or len(q) < 4:
        return False
    return q in _ws_norm(excerpt)


def _evidence_for_field(
    evidence: list[InstrumentEvidence], field_key: str
) -> InstrumentEvidence | None:
    key = (field_key or "").casefold().replace("-", "_")
    for ev in evidence:
        f = (ev.field or "").casefold().replace("-", "_")
        if f == key or key in f or f in key:
            return ev
    return None


def _has_separate_track_nte_signal(text: str) -> bool:
    """True only when opportunity/ceiling text clearly labels separate track NTEs.

    Reuses opportunity-constraint dual-track patterns — never invent tracks from
    any two unrelated dollar amounts (total + contingency, etc.).
    """
    cf = (text or "").casefold()
    if not cf.strip():
        return False
    amounts = _parse_nte_amounts(text)
    if len(amounts) < 2:
        return False
    dual_parts = "part 1" in cf and "part 2" in cf
    labels = _PART_LABEL_RE.findall(text)
    multi_label = len(labels) >= 2
    has_nte = (
        "not to exceed" in cf
        or "not-to-exceed" in cf
        or bool(re.search(r"\bnte\b", cf))
    )
    separate_nte = "separate" in cf and has_nte
    # Labeled ceiling strings: "Part 1 NTE $X" style (amount near part+nte).
    labeled_nte_hits = len(
        re.findall(
            r"(?:part\s*\d+|track\s*\d+)[^$\n]{0,48}"
            r"(?:nte|not[\s-]*to[\s-]*exceed)[^$\n]{0,24}\$",
            text or "",
            flags=re.IGNORECASE,
        )
    )
    if dual_parts and has_nte:
        return True
    if multi_label and has_nte:
        return True
    if separate_nte and multi_label:
        return True
    if labeled_nte_hits >= 2:
        return True
    return False


def bootstrap_delivery_tracks_from_opportunity(
    opportunity: Any | None,
) -> list[DeliveryTrack]:
    """Seed dual-NTE tracks from opportunity ceilings when LLM returned none.

    Only when ceiling/notes clearly indicate separate track NTEs (Part/track
    labels + NTE language). If unsure, leave delivery tracks empty.
    """
    blobs = _opportunity_budget_blobs(opportunity)
    if not blobs:
        return []
    joined = "\n".join(blobs)
    if not _has_separate_track_nte_signal(joined):
        logger.info(
            "pricing_instrument_opp_bootstrap skipped — no separate-track NTE signal"
        )
        return []
    amounts = _parse_nte_amounts(joined)
    if len(amounts) < 2:
        return []
    amounts = amounts[:4]
    labels = _PART_LABEL_RE.findall(joined)
    tracks: list[DeliveryTrack] = []
    for i, amt in enumerate(amounts):
        label = labels[i].strip() if i < len(labels) else f"Track {i + 1}"
        if label.lower().startswith("part"):
            label = re.sub(r"(?i)part\s*", "Part ", label).strip()
        tracks.append(
            DeliveryTrack(
                id=f"opp-track-{i + 1}",
                label=label[:200],
                nteAnnual=amt,
                billing="hourly",
            )
        )
    logger.info(
        "pricing_instrument_opp_bootstrap tracks=%d amounts=%s",
        len(tracks),
        amounts,
    )
    return tracks


def ground_instrument_against_excerpt(
    instrument: PricingInstrument,
    excerpt: str,
) -> PricingInstrument:
    """Drop / MANUAL-null fields whose evidence quote is missing or not in excerpt."""
    body = excerpt or ""
    grounded_ev = [
        ev for ev in (instrument.evidence or []) if _quote_in_excerpt(ev.quote, body)
    ]
    updates: dict[str, Any] = {"evidence": grounded_ev}

    bid = (instrument.bid_number or "").strip()
    if bid:
        ev = _evidence_for_field(grounded_ev, "bid_number") or _evidence_for_field(
            grounded_ev, "bid"
        )
        bid_ok = bool(
            (ev and _quote_in_excerpt(ev.quote, body))
            or _quote_in_excerpt(bid, body)
        )
        if not bid_ok:
            logger.info(
                "pricing_instrument_ground dropped unquoted bid_number=%s",
                bid[:40],
            )
            updates["bid_number"] = None

    new_tracks: list[PricingTrack] = []
    for track in instrument.tracks or []:
        nte = track.nte_annual
        if nte is None:
            new_tracks.append(track)
            continue
        # Need evidence quote mentioning this NTE, or the amount substring in excerpt.
        nte_s = f"{nte:,.0f}" if float(nte).is_integer() else f"{nte:,.2f}"
        nte_plain = str(int(nte)) if float(nte).is_integer() else str(nte)
        amount_in_excerpt = nte_s in body or nte_plain in body.replace(",", "")
        field_keys = (
            f"track.{track.id}.nte",
            f"{track.id}.nte",
            "nte_annual",
            "nte",
            track.id,
            track.label,
        )
        ev_hit = any(_evidence_for_field(grounded_ev, k) for k in field_keys)
        if ev_hit or amount_in_excerpt:
            new_tracks.append(track)
        else:
            logger.info(
                "pricing_instrument_ground nulling unquoted nte track=%s nte=%s",
                track.id,
                nte,
            )
            new_tracks.append(track.model_copy(update={"nte_annual": None}))
    updates["tracks"] = new_tracks

    # Identity labels must appear in the excerpt (form-native labels only).
    kept_fields: list[IdentityField] = []
    for field in instrument.identity_fields or []:
        label = (field.label or "").strip()
        if label and _quote_in_excerpt(label, body):
            kept_fields.append(field)
        elif label:
            logger.info(
                "pricing_instrument_ground dropped unquoted identity label=%s",
                label[:60],
            )
    updates["identity_fields"] = kept_fields

    return instrument.model_copy(update=updates)


def apply_buyer_form_confidence_gate(instrument: PricingInstrument) -> PricingInstrument:
    """Demote buyer_pricing_form below confidence threshold to kind=none."""
    if instrument.kind != "buyer_pricing_form":
        return instrument
    if float(instrument.confidence or 0) >= _BUYER_FORM_MIN_CONFIDENCE:
        return instrument
    logger.info(
        "pricing_instrument_confidence demote kind=buyer_pricing_form confidence=%.2f",
        float(instrument.confidence or 0),
    )
    return instrument.model_copy(update={"kind": "none"})


def _empty_failure() -> tuple[PricingInstrument, DeliveryConstraints]:
    return (
        PricingInstrument(kind="none", confidence=0.0),
        DeliveryConstraints(),
    )


def _compact_opportunity_for_prompt(opportunity: Any | None, *, max_chars: int = 6_000) -> str:
    blobs = _opportunity_budget_blobs(opportunity)
    opp = _opportunity_dict(opportunity)
    if "opportunity" in opp and isinstance(opp.get("opportunity"), dict):
        if "understanding" not in opp:
            opp = opp["opportunity"]
    scope = opp.get("scope") if isinstance(opp.get("scope"), dict) else {}
    mandatory = scope.get("mandatory") or []
    if isinstance(mandatory, list) and mandatory:
        blobs.append(
            "mandatory: "
            + "; ".join(str(m)[:120] for m in mandatory[:12] if str(m).strip())
        )
    optional = scope.get("optional") or []
    if isinstance(optional, list) and optional:
        blobs.append(
            "optional: "
            + "; ".join(str(m)[:120] for m in optional[:8] if str(m).strip())
        )
    out_of_scope = scope.get("outOfScope") or scope.get("out_of_scope") or []
    if isinstance(out_of_scope, list) and out_of_scope:
        blobs.append(
            "outOfScope: "
            + "; ".join(str(m)[:120] for m in out_of_scope[:8] if str(m).strip())
        )
    # Bidder-proposes / as-needed choices sometimes live on scope.notes or delivery.
    planish = _planish_dict(opportunity)
    delivery = planish.get("delivery") if isinstance(planish.get("delivery"), dict) else {}
    for key in ("bidderProposes", "bidder_proposes"):
        raw = delivery.get(key) or scope.get(key)
        if isinstance(raw, list) and raw:
            blobs.append(
                "bidderProposes: "
                + "; ".join(str(m)[:120] for m in raw[:8] if str(m).strip())
            )
            break
    und = opp.get("understanding") if isinstance(opp.get("understanding"), dict) else {}
    tl = und.get("timelineIntel") if isinstance(und.get("timelineIntel"), dict) else {}
    if not tl and isinstance(und.get("timeline_intel"), dict):
        tl = und["timeline_intel"]
    if tl:
        tl_bits = []
        for key in (
            "baseTerm",
            "base_term",
            "initialTermStart",
            "projectStart",
            "optionPeriods",
            "maxTerm",
            "max_term",
            "renewals",
            "quotesDue",
            "questionsDue",
        ):
            val = str(tl.get(key) or "").strip()
            if val:
                tl_bits.append(f"{key}={val[:80]}")
        if tl_bits:
            blobs.append("timeline: " + "; ".join(tl_bits[:10]))
    compliance = opp.get("compliance") if isinstance(opp.get("compliance"), dict) else {}
    items = compliance.get("items") or []
    if isinstance(items, list):
        reqs = [
            str(it.get("requirement") or "").strip()
            for it in items
            if isinstance(it, dict) and str(it.get("requirement") or "").strip()
        ]
        if reqs:
            blobs.append("compliance: " + "; ".join(reqs[:8]))
    text = "\n".join(b for b in blobs if b).strip()
    return text[:max_chars] if text else "(no opportunity pack)"


async def extract_pricing_and_delivery_constraints(
    rfp_text: str,
    opportunity: Any | None = None,
) -> tuple[PricingInstrument, DeliveryConstraints]:
    """LLM extract instrument + delivery; normalize; optional opportunity NTE bootstrap."""
    body = (rfp_text or "").strip()
    if not body:
        logger.warning(
            "%s skipped — empty RFP text; returning kind=none",
            AGENT,
        )
        return _empty_failure()

    excerpt = closing_package_excerpt(body, max_chars=18_000) or body[:20_000]
    opp_blob = _compact_opportunity_for_prompt(opportunity)
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    "Extract the pricing instrument and delivery constraints for THIS RFP.\n\n"
                    f"=== Opportunity pack (ceilings / scope hints) ===\n{opp_blob}\n\n"
                    f"=== RFP excerpt ===\n{excerpt}"
                ),
            },
        ],
        max_tokens=4096,
        temperature=0.05,
        agent_name=AGENT,
    )

    if not isinstance(raw, dict) or not raw:
        logger.warning(
            "%s LLM failure or empty JSON — kind=none confidence=0",
            AGENT,
        )
        return _empty_failure()

    inst_raw: Any = raw.get("instrument") if isinstance(raw.get("instrument"), dict) else None
    if inst_raw is None and isinstance(raw.get("pricingInstrument"), dict):
        inst_raw = raw["pricingInstrument"]
    if inst_raw is None and "kind" in raw:
        inst_raw = raw
    if inst_raw is None:
        inst_raw = {}

    del_raw = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
    if not del_raw and isinstance(raw.get("deliveryConstraints"), dict):
        del_raw = raw["deliveryConstraints"]

    instrument = normalize_instrument_payload(inst_raw)
    delivery = normalize_delivery_payload(del_raw)

    # Ground extracted facts to the excerpt (whitespace-normalized substring).
    instrument = ground_instrument_against_excerpt(instrument, excerpt)
    instrument = apply_buyer_form_confidence_gate(instrument)

    # Align delivery tracks from instrument when delivery empty but instrument has tracks.
    if not delivery.tracks and instrument.tracks:
        delivery = delivery.model_copy(
            update={
                "tracks": [
                    DeliveryTrack(
                        id=t.id,
                        label=t.label,
                        nteAnnual=t.nte_annual,
                        billing="hourly" if t.asks_hourly else "",
                    )
                    for t in instrument.tracks
                ],
                "non_commingle_tracks": len(instrument.tracks) > 1
                or delivery.non_commingle_tracks,
            }
        )

    # Opportunity dual-NTE bootstrap when still empty (labeled track NTEs only).
    if not delivery.tracks:
        boot = bootstrap_delivery_tracks_from_opportunity(opportunity)
        if boot:
            delivery = delivery.model_copy(
                update={
                    "tracks": boot,
                    "non_commingle_tracks": True,
                }
            )

    logger.info(
        "%s ok kind=%s confidence=%.2f tracks=%d delivery_tracks=%d",
        AGENT,
        instrument.kind,
        instrument.confidence,
        len(instrument.tracks),
        len(delivery.tracks),
    )
    return instrument, delivery
