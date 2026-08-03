"""Stable, correctable pricing rate card.

The card was rebuilt by re-parsing 00_Guide_Pricing prose on every run. Two
consequences, both landing on whoever reviews the budget:

  * the same guide could yield a different card run to run, so a budget that
    looked right yesterday needed re-checking today; and
  * any rate the parser missed became an unbound line item, and the fix had to
    be re-applied by hand to every proposal, forever.

Two mechanisms fix that without asking anyone to transcribe the guide:

  CACHE     — the card is keyed by a hash of the guide text. One guide version
              parses exactly once; every later run reuses that identical card.
              Edit the guide and the hash changes, so it re-parses on its own.

  OVERRIDES — a small version-controlled file merged over the parsed card. A
              human corrects a wrong rate or supplies a missed one ONCE, and it
              applies to every future proposal. Overrides win over the parse,
              because a person who checked the guide is better evidence than a
              regex over prose.

Overrides never invent: each entry still carries a source_doc and is bound by
the same rules as a parsed rate.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.pricing_rate_card import PricingRate, PricingRateCard

logger = logging.getLogger(__name__)

_DATA_DIR = Path(settings.database_path).parent
_CACHE_PATH = _DATA_DIR / "pricing_rate_card_cache.json"
# Version-controlled: this is human-authored truth, not derived data.
_OVERRIDES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "pricing_rate_overrides.json"
)


def guide_fingerprint(guide_text: str) -> str:
    """Stable id for a guide version — same text, same card."""
    return hashlib.sha256((guide_text or "").encode("utf-8")).hexdigest()[:32]


def _read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("pricing_rate_card_store unreadable path=%s error=%s", path, exc)
        return None


def load_overrides() -> list[PricingRate]:
    """Human-authored rate corrections, applied over whatever the parser found."""
    raw = _read_json(_OVERRIDES_PATH)
    if not raw:
        return []
    rows = raw.get("rates") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        logger.warning("pricing_rate_overrides malformed — expected a list of rates")
        return []

    out: list[PricingRate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(PricingRate.model_validate(row))
        except Exception as exc:  # pragma: no cover - surfaced, never fatal
            logger.warning(
                "pricing_rate_override rejected rate=%s error=%s",
                row.get("rateId") or row.get("rate_id"),
                exc,
            )
    return out


def apply_overrides(card: PricingRateCard, overrides: list[PricingRate]) -> PricingRateCard:
    """Merge overrides onto a parsed card by rate_id. Overrides win."""
    if not overrides:
        return card

    by_id: dict[str, PricingRate] = {r.rate_id: r for r in card.rates}
    replaced, added = 0, 0
    for override in overrides:
        if override.rate_id in by_id:
            replaced += 1
        else:
            added += 1
        by_id[override.rate_id] = override

    warnings = list(card.warnings)
    warnings.append(
        f"{replaced} rate(s) corrected and {added} added from "
        "data/pricing_rate_overrides.json"
    )
    logger.info(
        "pricing_rate_card overrides applied replaced=%d added=%d total=%d",
        replaced,
        added,
        len(by_id),
    )
    return card.model_copy(update={"rates": list(by_id.values()), "warnings": warnings})


def load_cached_card(fingerprint: str) -> PricingRateCard | None:
    """The card previously built for this exact guide version, if any."""
    raw = _read_json(_CACHE_PATH)
    if not isinstance(raw, dict):
        return None
    entry = raw.get(fingerprint)
    if not isinstance(entry, dict):
        return None
    try:
        return PricingRateCard.model_validate(entry)
    except Exception as exc:  # pragma: no cover
        logger.warning("pricing_rate_card_cache invalid entry error=%s", exc)
        return None


def store_card(fingerprint: str, card: PricingRateCard) -> None:
    """Remember this card for this guide version. Never fatal."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_json(_CACHE_PATH)
        data = existing if isinstance(existing, dict) else {}
        data[fingerprint] = card.model_dump(by_alias=True)
        # Keep only the most recent handful of guide versions.
        if len(data) > 5:
            for key in list(data)[:-5]:
                data.pop(key, None)
        _CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        logger.warning("pricing_rate_card_cache write failed error=%s", exc)


def build_stable_rate_card(guide_text: str) -> PricingRateCard:
    """Return the rate card for this guide: cached if seen, freshly parsed if not.

    Overrides are applied in both paths, so correcting a rate takes effect on
    the next run without clearing any cache.
    """
    from app.services.pricing_rate_card_builder import (
        build_pricing_rate_card_from_guide_text,
    )

    fingerprint = guide_fingerprint(guide_text)
    overrides = load_overrides()

    cached = load_cached_card(fingerprint)
    if cached is not None:
        logger.info(
            "pricing_rate_card cache_hit fingerprint=%s rates=%d",
            fingerprint,
            len(cached.rates),
        )
        return apply_overrides(cached, overrides)

    card = build_pricing_rate_card_from_guide_text(guide_text)
    # Cache the PARSED card, not the overridden one, so an override edit is
    # picked up next run instead of being frozen into the cache.
    store_card(fingerprint, card)
    logger.info(
        "pricing_rate_card parsed fingerprint=%s rates=%d",
        fingerprint,
        len(card.rates),
    )
    return apply_overrides(card, overrides)
