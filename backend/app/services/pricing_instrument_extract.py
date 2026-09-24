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
    "mandatoryDeliverables": ["string — EVERY numbered Scope of Services / SOW line when present"],
    "bidderProposes": ["string"],
    "outOfScope": ["string"],
    "horizon": {"baseTerm": "string", "renewals": "string", "maxTerm": "string"},
    "nonCommingleTracks": bool,
    "buyerOwnsDeliverables": bool,
    "letterProposalGate": bool,
    "evidence": [{"field": "string", "quote": "verbatim quote", "locator": "string"}]
  }
}

Rules:
- kind=buyer_pricing_form ONLY when THIS RFP has a fillable Pricing / Quotation / Proposal Pricing form
  (identity + rate/hours lines and/or Part tracks with NTEs).
- kind=personnel_loading when THIS RFP asks for role/labor-category hourly rate tables.
- kind=phased_fee_schedule when fees are by phase/deliverable with no buyer fillable form.
- kind=none when no cost instrument is found or confidence is low.
- Put only THIS form's identity labels in identityFields (e.g. COMPANY NAME,
  CONTACT PERSON, CONTACT EMAIL when those labels appear). Use valueSource
  "companyfacts" for agency identity fills; never invent FEIN/phone/fax.
- Track nteAnnual: use RFP form figures when present; otherwise use the
  Opportunity pack dual-track ceilings when they clearly bind Part/track NTEs.
- Signature: set required flags when the form shows Printed Name / Signature /
  Title / Date.
- bidNumber: copy ONLY from THIS RFP; never invent or borrow from another solicitation.
- Every non-null bidNumber, NTE, and form field MUST have an evidence quote from THIS excerpt.
- Align delivery.tracks ids/labels with instrument.tracks when both exist.
- mandatoryDeliverables: when THIS RFP lists a numbered Scope of Services / Scope of Work /
  services list, copy EACH line in order (full list — do not truncate or summarize mid-list).
  Numbered lists often continue after a repeated section header or page break — keep
  collecting until the consecutive numbering ends (e.g. 1…14, not stop at 12).
- buyerOwnsDeliverables: true when Draft Agreement / contract says deliverables / work
  product are the buyer's exclusive property (or equivalent work-for-hire ownership).
- letterProposalGate: true when work starts only after a Letter Proposal / task order /
  written authorization for each assignment (on-call / as-needed contracts).
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
            raw.get("mandatoryDeliverables") or raw.get("mandatory_deliverables"),
            limit=40,
        ),
        bidderProposes=_normalize_str_list(
            raw.get("bidderProposes") or raw.get("bidder_proposes")
        ),
        outOfScope=_normalize_str_list(raw.get("outOfScope") or raw.get("out_of_scope")),
        horizon=_normalize_horizon(raw.get("horizon")),
        nonCommingleTracks=bool(
            raw.get("nonCommingleTracks", raw.get("non_commingle_tracks", False))
        ),
        buyerOwnsDeliverables=bool(
            raw.get("buyerOwnsDeliverables", raw.get("buyer_owns_deliverables", False))
        ),
        letterProposalGate=bool(
            raw.get("letterProposalGate", raw.get("letter_proposal_gate", False))
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
    *,
    secondary_corpus: str = "",
) -> PricingInstrument:
    """Drop / MANUAL-null *extract* fields whose quote or amount is not in corpus.

    Companyfacts-backed identity fields are kept (they are not RFP quotes).
    Track NTEs may be grounded against the RFP excerpt OR ``secondary_corpus``
    (opportunity ceilings) so dual NTEs survive when only the opportunity pack
    states the dollars.
    """
    body = excerpt or ""
    secondary = secondary_corpus or ""
    amount_corpus = f"{body}\n{secondary}"
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
        nte_s = f"{nte:,.0f}" if float(nte).is_integer() else f"{nte:,.2f}"
        nte_plain = str(int(nte)) if float(nte).is_integer() else str(nte)
        amount_in_corpus = (
            nte_s in amount_corpus
            or nte_plain in amount_corpus.replace(",", "")
        )
        field_keys = (
            f"track.{track.id}.nte",
            f"{track.id}.nte",
            "nte_annual",
            "nte",
            track.id,
            track.label,
        )
        ev_hit = any(_evidence_for_field(grounded_ev, k) for k in field_keys)
        if ev_hit or amount_in_corpus:
            new_tracks.append(track)
        else:
            logger.info(
                "pricing_instrument_ground nulling unquoted nte track=%s nte=%s",
                track.id,
                nte,
            )
            new_tracks.append(track.model_copy(update={"nte_annual": None}))
    updates["tracks"] = new_tracks

    # Keep companyfacts identity always; extract labels must appear on THIS form.
    kept_fields: list[IdentityField] = []
    for field in instrument.identity_fields or []:
        src = (field.value_source or "").casefold()
        if src == "companyfacts":
            kept_fields.append(field)
            continue
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


# Form-native labels we may promote to companyfacts-backed identity when present
# on the buyer form excerpt (principle: label must appear; values come from facts).
_FORM_IDENTITY_LABELS: tuple[tuple[str, str], ...] = (
    ("company_name", "COMPANY NAME"),
    ("contact_person", "CONTACT PERSON"),
    ("contact_email", "CONTACT EMAIL"),
)


def ensure_buyer_form_completeness(
    instrument: PricingInstrument,
    excerpt: str,
) -> PricingInstrument:
    """Fill gaps on buyer forms: form-native identity labels + signature defaults.

    Does not invent FEIN/phone. Only adds identity rows when the form excerpt
    literally contains the label. Signature defaults when the excerpt mentions
    signature / printed name.
    """
    if instrument.kind != "buyer_pricing_form":
        return instrument
    body = excerpt or ""
    body_cf = body.casefold()
    updates: dict[str, Any] = {}

    existing_keys = {
        (f.key or "").casefold() for f in (instrument.identity_fields or [])
    }
    fields = list(instrument.identity_fields or [])
    for key, label in _FORM_IDENTITY_LABELS:
        if key in existing_keys:
            continue
        if _quote_in_excerpt(label, body):
            fields.append(
                IdentityField(key=key, label=label, valueSource="companyfacts")
            )
            existing_keys.add(key)
    if fields != list(instrument.identity_fields or []):
        updates["identity_fields"] = fields
        logger.info(
            "pricing_instrument_ensure identity_fields=%s",
            [f.key for f in fields],
        )

    # Structural fallback: bid + NTE tracks → always complete the companyfacts
    # identity trio (partial LLM identity must not omit CONTACT EMAIL).
    final_fields = updates.get("identity_fields", fields)
    structural = bool(
        (instrument.bid_number or "").strip()
        and any(t.nte_annual is not None for t in (instrument.tracks or []))
    )
    if structural:
        have = {(f.key or "").casefold() for f in final_fields}
        added = False
        for key, label in _FORM_IDENTITY_LABELS:
            if key in have:
                continue
            final_fields.append(
                IdentityField(key=key, label=label, valueSource="companyfacts")
            )
            have.add(key)
            added = True
        if added or final_fields != list(instrument.identity_fields or []):
            updates["identity_fields"] = final_fields
            logger.info(
                "pricing_instrument_ensure identity_fields structural complete=%s",
                [f.key for f in final_fields],
            )

    sig = instrument.signature
    sig_mentioned = any(
        tok in body_cf
        for tok in ("signature", "printed name", "sign here", "authorized signature")
    )
    # Default signature block for multi-track buyer forms even if excerpt missed it.
    need_sig_default = sig_mentioned or (
        len(instrument.tracks or []) >= 1
        and (instrument.bid_number or "").strip()
    )
    if need_sig_default and (
        sig is None
        or not (
            sig.printed_name_required
            or sig.title_required
            or sig.signature_required
            or sig.date_required
        )
    ):
        updates["signature"] = PricingSignature(
            printedNameRequired=True,
            titleRequired=True,
            signatureRequired=True,
            dateRequired=True,
        )
        logger.info("pricing_instrument_ensure signature defaults")

    rules = list(instrument.internal_note_rules or [])
    if len(instrument.tracks or []) >= 2:
        for rule in ("one_hourly_per_track", "do_not_commingle_ntes"):
            if rule not in rules:
                rules.append(rule)
        updates["internal_note_rules"] = rules

    if not updates:
        return instrument
    return instrument.model_copy(update=updates)


def merge_delivery_ntes_onto_instrument(
    instrument: PricingInstrument,
    delivery: DeliveryConstraints,
) -> PricingInstrument:
    """Copy delivery-track NTEs onto instrument tracks when extract left them null."""
    if instrument.kind != "buyer_pricing_form" or not instrument.tracks:
        return instrument
    by_id = {t.id: t for t in (delivery.tracks or []) if t.id}
    by_label = {
        (t.label or "").casefold(): t for t in (delivery.tracks or []) if t.label
    }
    new_tracks: list[PricingTrack] = []
    changed = False
    for track in instrument.tracks:
        if track.nte_annual is not None:
            new_tracks.append(track)
            continue
        hit = by_id.get(track.id) or by_label.get((track.label or "").casefold())
        if hit is not None and hit.nte_annual is not None:
            new_tracks.append(
                track.model_copy(update={"nte_annual": float(hit.nte_annual)})
            )
            changed = True
        else:
            new_tracks.append(track)
    if not changed:
        return instrument
    logger.info("pricing_instrument_merge_delivery_ntes tracks=%s", len(new_tracks))
    return instrument.model_copy(update={"tracks": new_tracks})


def instrument_needs_refresh(instrument: PricingInstrument | None) -> bool:
    """True when Phase 3.5 should re-extract (missing or incomplete buyer form)."""
    if instrument is None:
        return True
    # Demotion artifact: kind=none but form payload still present → repair.
    if instrument.kind == "none":
        return bool(
            (instrument.bid_number or "").strip() or (instrument.tracks or [])
        )
    if instrument.kind != "buyer_pricing_form":
        return False
    if float(instrument.confidence or 0) < _BUYER_FORM_MIN_CONFIDENCE:
        return True
    identity_keys = {
        (f.key or "").casefold() for f in (instrument.identity_fields or [])
    }
    required_identity = {k for k, _ in _FORM_IDENTITY_LABELS}
    if not required_identity.issubset(identity_keys):
        return True
    tracks = instrument.tracks or []
    if len(tracks) >= 2 and all(t.nte_annual is None for t in tracks):
        return True
    return False


def pricing_form_focus_excerpt(rfp_text: str, *, max_chars: int = 14_000) -> str:
    """Prefer windows around Pricing Form / Bid Number / contact labels."""
    body = (rfp_text or "").strip()
    if not body:
        return ""
    patterns = (
        r"proposal\s+pricing\s+form",
        r"pricing\s+proposal\s+form",
        r"bid\s+number",
        r"company\s+name",
        r"contact\s+person",
        r"contact\s+email",
        r"printed\s+name",
        r"not\s+to\s+exceed",
    )
    span = 3500
    windows: list[tuple[int, int]] = []
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I):
            windows.append((max(0, m.start() - span), min(len(body), m.end() + span)))
    if not windows:
        return ""
    windows.sort()
    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1] + 200:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts: list[str] = []
    used = 0
    for start, end in merged:
        chunk = body[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used]
        if chunk.strip():
            parts.append(chunk)
            used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


def scope_services_focus_excerpt(rfp_text: str, *, max_chars: int = 12_000) -> str:
    """Prefer the Scope of Services / Work window with the longest 1..N list.

    RFPs often repeat the section header across a page break mid-list; score each
    hit by consecutive numbered lines so trailing items (past a form-feed) win
    over short contract boilerplate that also says "Scope of Services".
    """
    body = (rfp_text or "").strip()
    if not body:
        return ""
    patterns = (
        r"scope\s+of\s+services",
        r"scope\s+of\s+work",
        r"services\s+to\s+be\s+performed",
    )
    back, forward = 400, 9_000
    best = ""
    best_score = -1
    seen_starts: set[int] = set()
    for pat in patterns:
        for m in re.finditer(pat, body, flags=re.I):
            start = max(0, m.start() - back)
            if start in seen_starts:
                continue
            seen_starts.add(start)
            end = min(len(body), m.end() + forward)
            chunk = body[start:end]
            if len(chunk) > max_chars:
                chunk = chunk[:max_chars]
            score = len(harvest_numbered_scope_lines(chunk))
            if score > best_score:
                best_score = score
                best = chunk
    return best


_NUMBERED_SCOPE_LINE_RE = re.compile(
    r"(?m)^\s*(\d{1,2})[.)]\s+(\S.+?)\s*$"
)


def harvest_numbered_scope_lines(text: str, *, max_items: int = 40) -> list[str]:
    """Longest consecutive 1..N numbered list from Scope windows (structure only).

    Prefer this over a truncated / paraphrased LLM mandatoryDeliverables list when
    the harvest is longer. Not capability synonym matching — verbatim line text.
    """
    body = text or ""
    if not body.strip():
        return []
    # Collect (n, text) in document order; keep first occurrence of each n.
    seen: dict[int, str] = {}
    order: list[int] = []
    for m in _NUMBERED_SCOPE_LINE_RE.finditer(body):
        n = int(m.group(1))
        if n < 1 or n > max_items:
            continue
        line = re.sub(r"\s+", " ", m.group(2)).strip()
        if len(line) < 8:
            continue
        # Skip checklist / cert noise that starts mid-doc with 1. after Scope.
        if n == 1 and order and max(order) >= 3:
            # Start of a new list — evaluate later if longer than current run.
            pass
        if n not in seen:
            seen[n] = line[:350]
            order.append(n)

    # Find longest run starting at 1 with consecutive integers.
    best: list[str] = []
    if 1 in seen:
        run = [seen[1]]
        i = 2
        while i in seen:
            run.append(seen[i])
            i += 1
        best = run
    return best[:max_items]


def prefer_harvested_mandatory(
    delivery: DeliveryConstraints,
    *,
    scope_excerpt: str,
) -> DeliveryConstraints:
    """Prefer consecutive numbered Scope lines from the RFP when they cover more."""
    harvested = harvest_numbered_scope_lines(scope_excerpt)
    current = list(delivery.mandatory_deliverables or [])
    if not harvested:
        return delivery
    if len(harvested) >= max(len(current), 1):
        if harvested != current:
            logger.info(
                "pricing_instrument_extract mandatory harvest len=%s (was llm len=%s)",
                len(harvested),
                len(current),
            )
            return delivery.model_copy(update={"mandatory_deliverables": harvested})
    return delivery


_BUYER_OWNS_SIGNALS = (
    "exclusive property",
    "sole property of",
    "shall be the property of",
    "become the property of",
    "work product shall belong",
    "deliverables shall be owned",
    "all rights, title and interest",
    "all right, title and interest",
)


def infer_ownership_and_letter_gate(
    delivery: DeliveryConstraints,
    *,
    rfp_excerpt: str,
) -> DeliveryConstraints:
    """Fill buyerOwns / letterProposalGate from contract wording when LLM misses them."""
    body = (rfp_excerpt or "").casefold()
    if not body:
        return delivery
    updates: dict[str, bool] = {}
    if not delivery.buyer_owns_deliverables and any(s in body for s in _BUYER_OWNS_SIGNALS):
        updates["buyer_owns_deliverables"] = True
    if not delivery.letter_proposal_gate and (
        "letter proposal" in body
        or "no services shall be provided until" in body
        or ("task order" in body and "authoriz" in body)
    ):
        updates["letter_proposal_gate"] = True
    if not updates:
        return delivery
    logger.info(
        "pricing_instrument_extract inferred flags %s",
        ",".join(updates),
    )
    return delivery.model_copy(update=updates)


def apply_buyer_form_confidence_gate(instrument: PricingInstrument) -> PricingInstrument:
    """Demote weak buyer forms; rescue structurally complete ones.

    If the LLM under-scores confidence but we already have bid + NTE tracks,
    keep ``buyer_pricing_form`` and floor confidence at the render threshold.
    """
    if instrument.kind != "buyer_pricing_form":
        return instrument
    conf = float(instrument.confidence or 0)
    if conf >= _BUYER_FORM_MIN_CONFIDENCE:
        return instrument
    tracks = instrument.tracks or []
    structural = bool(
        (instrument.bid_number or "").strip()
        and len(tracks) >= 1
        and any(t.nte_annual is not None for t in tracks)
    )
    if structural:
        logger.info(
            "pricing_instrument_confidence structural rescue confidence=%.2f → %.2f "
            "bid=%s tracks=%s",
            conf,
            _BUYER_FORM_MIN_CONFIDENCE,
            (instrument.bid_number or "")[:40],
            len(tracks),
        )
        return instrument.model_copy(
            update={"confidence": _BUYER_FORM_MIN_CONFIDENCE}
        )
    logger.info(
        "pricing_instrument_confidence demote kind=buyer_pricing_form confidence=%.2f",
        conf,
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
            + "; ".join(str(m)[:120] for m in mandatory[:40] if str(m).strip())
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

    excerpt = closing_package_excerpt(body, max_chars=12_000) or body[:12_000]
    form_focus = pricing_form_focus_excerpt(body, max_chars=10_000)
    scope_focus = scope_services_focus_excerpt(body, max_chars=12_000)
    prefix_parts = [p for p in (scope_focus, form_focus) if p]
    if prefix_parts:
        excerpt = ("\n\n---\n\n".join(prefix_parts) + "\n\n---\n\n" + excerpt)[:28_000]
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
    # Harvest from Scope windows only — full excerpt mixes cert/checklist "1." noise.
    delivery = prefer_harvested_mandatory(
        delivery, scope_excerpt=scope_focus or excerpt
    )
    delivery = infer_ownership_and_letter_gate(delivery, rfp_excerpt=excerpt)

    # Ground extract facts to RFP excerpt; opportunity ceilings may back NTEs.
    instrument = ground_instrument_against_excerpt(
        instrument, excerpt, secondary_corpus=opp_blob
    )
    instrument = ensure_buyer_form_completeness(instrument, excerpt)
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

    # Push delivery NTEs back onto instrument tracks left null by grounding.
    instrument = merge_delivery_ntes_onto_instrument(instrument, delivery)

    logger.info(
        "%s ok kind=%s confidence=%.2f identity=%d tracks=%d delivery_tracks=%d",
        AGENT,
        instrument.kind,
        instrument.confidence,
        len(instrument.identity_fields or []),
        len(instrument.tracks),
        len(delivery.tracks),
    )
    return instrument, delivery
