"""Load and apply Fact Ledger overrides (T4.0 resolution without cleaning KB)."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.models.fact_ledger import ClaimClass, FactLedgerOverride, LedgerClaim

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "fact_ledger_overrides.yaml"


def load_fact_ledger_overrides(
    path: Path | str | None = None,
) -> list[FactLedgerOverride]:
    """Load overrides from YAML. Missing/empty file → []. Never invents values."""
    target = Path(path) if path is not None else _DEFAULT_PATH
    if not target.is_file():
        logger.info("fact_ledger_overrides_missing path=%s", target)
        return []
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.error("fact_ledger_overrides_load_failed path=%s err=%s", target, exc)
        raise

    rows = raw.get("overrides") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        logger.warning("fact_ledger_overrides_invalid_shape path=%s", target)
        return []

    overrides: list[FactLedgerOverride] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            logger.warning("fact_ledger_overrides_skip_row index=%s", index)
            continue
        try:
            overrides.append(FactLedgerOverride.model_validate(row))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fact_ledger_overrides_invalid_row index=%s err=%s", index, str(exc)[:160]
            )
    logger.info(
        "fact_ledger_overrides_loaded path=%s count=%s", target, len(overrides)
    )
    return overrides


def apply_overrides_to_claims(
    claims: list[LedgerClaim],
    overrides: list[FactLedgerOverride],
) -> tuple[list[LedgerClaim], list[str]]:
    """Replace conflicting claim groups with a single override-backed claim.

    Returns (resolved_claims, resolution_notes). Claims whose keys are not
    overridden are left unchanged — including unresolved conflicts.
    """
    if not overrides:
        return list(claims), []

    by_key: dict[tuple[str, str, str], FactLedgerOverride] = {}
    for ov in overrides:
        key = (ov.subject_id, ov.claim_class.value, ov.field_name)
        by_key[key] = ov

    kept: list[LedgerClaim] = []
    notes: list[str] = []
    consumed_keys: set[tuple[str, str, str]] = set()

    for claim in claims:
        key = (claim.subject_id, claim.claim_class.value, claim.field_name)
        if key not in by_key:
            kept.append(claim)
            continue
        if key in consumed_keys:
            continue
        consumed_keys.add(key)
        ov = by_key[key]
        kept.append(_claim_from_override(ov, prior=claim))
        notes.append(_note_for_override(ov, prior_values=_prior_nums(claims, key)))

    # Overrides with no prior KB claim still inject authority.
    for key, ov in by_key.items():
        if key in consumed_keys:
            continue
        kept.append(_claim_from_override(ov, prior=None))
        notes.append(_note_for_override(ov, prior_values=[]))
        consumed_keys.add(key)
        logger.info(
            "fact_ledger_override_injected subject=%s field=%s value=%s",
            ov.subject_id,
            ov.field_name,
            ov.value_text,
        )

    logger.info(
        "fact_ledger_overrides_applied overrides=%s notes=%s claims_in=%s claims_out=%s",
        len(overrides),
        len(notes),
        len(claims),
        len(kept),
    )
    return kept, notes


def _prior_nums(
    claims: list[LedgerClaim], key: tuple[str, str, str]
) -> list[float]:
    subject_id, claim_class, field_name = key
    out: list[float] = []
    for claim in claims:
        if (
            claim.subject_id == subject_id
            and claim.claim_class.value == claim_class
            and claim.field_name == field_name
            and claim.value_number is not None
        ):
            out.append(float(claim.value_number))
    return sorted({round(n, 2) for n in out})


def _claim_from_override(
    ov: FactLedgerOverride, *, prior: LedgerClaim | None
) -> LedgerClaim:
    claim_id = f"override:{ov.subject_id}:{ov.field_name}"
    return LedgerClaim(
        claimId=claim_id,
        claimClass=ov.claim_class,
        subjectType=ov.subject_type,
        subjectId=ov.subject_id,
        fieldName=ov.field_name,
        valueText=ov.value_text,
        valueNumber=ov.value_number,
        unit=ov.unit,
        sourceDoc="override",
        sourceLocator=ov.approved_by or "fact_ledger_overrides",
        verbatimSnippet=ov.reason or ov.value_text,
        confidence=1.0,
        conflictGroup=None,
    )


def _note_for_override(ov: FactLedgerOverride, *, prior_values: list[float]) -> str:
    prior = (
        f" (KB had {', '.join(str(v) for v in prior_values)})" if prior_values else ""
    )
    who = ov.approved_by or "unsigned"
    why = f" — {ov.reason}" if ov.reason else ""
    return (
        f"Resolved {ov.claim_class.value} for {ov.subject_id} "
        f"({ov.field_name}) → {ov.value_text} via override (approved_by={who})"
        f"{prior}{why}"
    )
