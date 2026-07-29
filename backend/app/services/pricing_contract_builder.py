"""Build PricingContract from Stage 1 + RFP text. Never invent media spend."""

from __future__ import annotations

import logging
import re

from app.models.pricing_contract import PricingContract

logger = logging.getLogger(__name__)

_COMMISSION_MODEL_RE = re.compile(
    r"\b(?:85\s*/\s*15|(?<!\bno\s)(?<!\bwithout\s)media\s+commission|"
    r"commission\s+model|traditional\s+media\s+commission|net\s+invoicing)\b",
    re.I,
)
_NO_COMMISSION_RE = re.compile(
    r"\b(?:no\s+media\s+commission|without\s+(?:media\s+)?commission|"
    r"hourly\s+(?:labor|rates)|labor\s+categor|"
    r"fully\s+burdened\s+hourly)\b",
    re.I,
)
_HOURLY_RE = re.compile(r"\bhourly\b", re.I)
_PHASED_RE = re.compile(r"\b(?:phased\s+fee|fixed\s+fee|lump[\s-]?sum)\b", re.I)
_RATE_RE = re.compile(
    r"(?:(?:commission(?:\s+rate)?|agency\s+commission)\s*(?:of|at|=|:)?\s*)?"
    r"(\d{1,2}(?:\.\d+)?)\s*%|"
    r"\b(85\s*/\s*15)\b",
    re.I,
)
_MEDIA_SPEND_RE = re.compile(
    r"(?:"
    r"(?:annual\s+)?(?:media|paid\s+media|placements?)\s+"
    r"(?:budget|spend|buys?|placements?)?\s*(?:of|at|=|:|approximately|approx\.?|~)?\s*"
    r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)"
    r"|"
    r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)\s+"
    r"(?:in\s+)?(?:annual\s+)?(?:paid\s+)?media"
    r")",
    re.I,
)


def _parse_money(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _extract_commission_rate(blob: str) -> float | None:
    match = _RATE_RE.search(blob)
    if not match:
        return None
    if match.group(2):
        return 0.15
    pct = float(match.group(1))
    if pct <= 0 or pct > 100:
        return None
    return round(pct / 100.0, 4)


def _extract_media_spend(blob: str) -> float | None:
    match = _MEDIA_SPEND_RE.search(blob)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    amount = _parse_money(raw or "")
    if amount is None or amount <= 0:
        return None
    # Guard against tiny false positives (e.g. "$15" from 15%).
    if amount < 1000:
        return None
    return amount


def build_pricing_contract(
    *,
    stage_one_text: str = "",
    rfp_text: str = "",
) -> PricingContract:
    """Deterministic fee-model contract. Never invents mediaSpendAnnual."""
    blob = f"{stage_one_text or ''}\n{rfp_text or ''}"
    notes: list[str] = []

    has_commission = bool(_COMMISSION_MODEL_RE.search(blob))
    no_commission = bool(_NO_COMMISSION_RE.search(blob))
    if no_commission:
        # Explicit negation wins over residual "commission" tokens in the same blob.
        has_commission = False
    media_spend = _extract_media_spend(blob)
    rate = _extract_commission_rate(blob) if has_commission or media_spend else None

    if media_spend is not None:
        notes.append(f"evidenced media spend annual={media_spend:,.2f}")
    if rate is not None:
        notes.append(f"evidenced commission rate={rate}")

    if no_commission and not has_commission:
        fee_model = "hourly" if _HOURLY_RE.search(blob) else (
            "phased_fee" if _PHASED_RE.search(blob) else "unknown"
        )
        confidence: str = "medium" if fee_model != "unknown" else "low"
        notes.append("explicit non-commission / hourly language")
        contract = PricingContract(
            feeModel=fee_model,
            mediaSpendAnnual=None,
            commissionRate=None,
            evidenceNotes=notes,
            confidence=confidence,  # type: ignore[arg-type]
        )
    elif has_commission and media_spend is not None:
        contract = PricingContract(
            feeModel="commission",
            mediaSpendAnnual=media_spend,
            commissionRate=rate,
            evidenceNotes=notes or ["commission + media spend evidenced"],
            confidence="high" if rate is not None else "medium",
        )
    elif has_commission:
        contract = PricingContract(
            feeModel="commission",
            mediaSpendAnnual=None,
            commissionRate=rate,
            evidenceNotes=notes or ["commission language without evidenced media spend"],
            confidence="medium" if rate is not None else "low",
        )
    elif media_spend is not None and rate is not None:
        contract = PricingContract(
            feeModel="hybrid",
            mediaSpendAnnual=media_spend,
            commissionRate=rate,
            evidenceNotes=notes,
            confidence="medium",
        )
    elif _HOURLY_RE.search(blob):
        contract = PricingContract(
            feeModel="hourly",
            evidenceNotes=notes + ["hourly language"],
            confidence="medium",
        )
    elif _PHASED_RE.search(blob):
        contract = PricingContract(
            feeModel="phased_fee",
            evidenceNotes=notes + ["phased/fixed fee language"],
            confidence="medium",
        )
    else:
        contract = PricingContract(
            feeModel="unknown",
            evidenceNotes=notes or ["insufficient fee-model evidence"],
            confidence="low",
        )

    logger.info(
        "pricing_contract fee_model=%s media_spend=%s rate=%s confidence=%s",
        contract.fee_model,
        contract.media_spend_annual,
        contract.commission_rate,
        contract.confidence,
    )
    return contract


def format_pricing_contract_for_prompt(contract: PricingContract) -> str:
    """Locked fields block injected into Stage 3 budget user content."""
    spend = (
        f"{contract.media_spend_annual:,.2f}"
        if contract.media_spend_annual is not None
        else "null (DO NOT INVENT)"
    )
    rate = (
        f"{contract.commission_rate}"
        if contract.commission_rate is not None
        else "null (DO NOT INVENT)"
    )
    notes = "; ".join(contract.evidence_notes) or "(none)"
    return (
        "=== LOCKED PricingContract (deterministic — do not contradict) ===\n"
        f"feeModel: {contract.fee_model}\n"
        f"mediaSpendAnnual: {spend}\n"
        f"commissionRate: {rate}\n"
        f"confidence: {contract.confidence}\n"
        f"evidence: {notes}\n"
        "Rules:\n"
        "- If feeModel is not commission/hybrid: do NOT emit commission line items or commission dollars.\n"
        "- If mediaSpendAnnual is null: do NOT invent media $ or commission $. "
        "Retain commission shape only via MANUAL FILL placeholders when feeModel is commission/hybrid.\n"
        "- If mediaSpendAnnual is set: clientMediaPassthrough MUST equal it; "
        "agency commission fee MUST equal commissionRate × mediaSpendAnnual when rate is known.\n"
    )
