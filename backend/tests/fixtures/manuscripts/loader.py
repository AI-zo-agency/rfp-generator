"""Load sanitised synthetic manuscript fixtures for validator regression tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).resolve().parent

FIXTURE_NAMES: tuple[str, ...] = (
    "gsu_inconsistent_years",
    "cvvb_v1_duplication_budget",
    "cvvb_v2_truncation_orphan_commission",
    "known_good_clean",
    "known_good_budget_narrative",
)


def load_fixture(
    name: str,
) -> tuple[ProposalDraft, ProposalResearchCache | None, RfpRecord, dict]:
    """Load draft, optional research, RFP, and expected findings for *name*."""
    logger.debug("loading manuscript fixture name=%s", name)
    draft_path = FIXTURES_DIR / f"{name}.draft.json"
    research_path = FIXTURES_DIR / f"{name}.research.json"
    rfp_path = FIXTURES_DIR / f"{name}.rfp.json"
    expected_path = FIXTURES_DIR / f"{name}.expected_findings.json"

    if not draft_path.is_file():
        raise FileNotFoundError(f"Missing draft fixture: {draft_path}")
    if not rfp_path.is_file():
        raise FileNotFoundError(f"Missing RFP fixture: {rfp_path}")
    if not expected_path.is_file():
        raise FileNotFoundError(f"Missing expected findings: {expected_path}")

    draft = ProposalDraft.model_validate(_read_json(draft_path))
    rfp = RfpRecord.model_validate(_read_json(rfp_path))
    expected = _read_json(expected_path)

    research: ProposalResearchCache | None = None
    if research_path.is_file():
        research = ProposalResearchCache.model_validate(_read_json(research_path))
        logger.debug(
            "loaded research cache for fixture name=%s has_budget=%s",
            name,
            research.budget is not None,
        )
    else:
        logger.debug("no research.json for fixture name=%s", name)

    logger.info(
        "loaded manuscript fixture name=%s sections=%s expected_critical=%s",
        name,
        len(draft.sections),
        len(expected.get("critical", [])) if isinstance(expected, dict) else None,
    )
    return draft, research, rfp, expected


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Fixture root must be an object: {path}")
    return data
