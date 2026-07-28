"""Load / attach FactLedger on proposal research cache (W4 T4.3 store seam)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.models.fact_ledger import FactLedger, LedgerClaim
from app.models.proposal import ProposalResearchCache
from app.services.fact_ledger_builder import build_fact_ledger

logger = logging.getLogger(__name__)


def ledger_from_research(research: ProposalResearchCache | None) -> FactLedger | None:
    """Parse fact_ledger payload from research cache if present."""
    if research is None or research.fact_ledger is None:
        return None
    raw = research.fact_ledger
    if isinstance(raw, FactLedger):
        return raw
    if isinstance(raw, dict):
        try:
            return FactLedger.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fact_ledger_parse_failed: %s", str(exc)[:200])
            return None
    return None


def attach_fact_ledger(
    research: ProposalResearchCache,
    ledger: FactLedger,
) -> ProposalResearchCache:
    """Persist ledger dict onto research cache (aliased JSON field)."""
    payload = ledger.model_dump(by_alias=True, mode="json")
    logger.info(
        "fact_ledger_attached rfp_id=%s version=%s conflicts=%s",
        research.rfp_id,
        ledger.version,
        len(ledger.blocking_conflicts),
    )
    return research.model_copy(update={"fact_ledger": payload})


def build_and_attach_ledger(
    research: ProposalResearchCache,
    *,
    claims: list[LedgerClaim],
    people_names: dict[str, str] | None = None,
    version: str | None = None,
    load_default_overrides: bool = True,
) -> ProposalResearchCache:
    """Build from claim candidates and attach — used by pipeline / tests.

    Default overrides from ``app/data/fact_ledger_overrides.yaml`` resolve KB
    conflicts to one authoritative value for generation.
    """
    built_at = datetime.now(timezone.utc).isoformat()
    ver = version or f"ledger-{research.rfp_id}-{built_at[:19]}"
    ledger = build_fact_ledger(
        version=ver,
        built_at=built_at,
        claims=claims,
        people_names=people_names,
        load_default_overrides=load_default_overrides,
    )
    return attach_fact_ledger(research, ledger)


def ledger_as_drafting_state(ledger: FactLedger | None) -> dict[str, Any] | None:
    """Shape for DraftingGraphState.fact_ledger."""
    if ledger is None:
        return None
    return ledger.model_dump(by_alias=True, mode="json")
