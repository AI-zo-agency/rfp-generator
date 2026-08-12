#!/usr/bin/env python3
"""Match KB case studies to one RFP (CLI mirror of proposals-page button).

Usage:
  cd backend && source .venv/bin/activate
  python scripts/match_case_studies_for_rfp.py <rfp_id>
  python scripts/match_case_studies_for_rfp.py <rfp_id> --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


async def _run(rfp_id: str, *, as_json: bool) -> int:
    from app.services.rfp_repository import get_rfp
    from app.services.proposal_case_study_match import match_case_studies_for_rfp

    rfp = get_rfp(rfp_id)
    if not rfp:
        print(f"RFP not found: {rfp_id}", file=sys.stderr)
        return 1

    result = await match_case_studies_for_rfp(rfp)
    payload = result.model_dump(by_alias=True)

    if as_json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"RFP: {rfp.title} ({rfp_id})")
    print(result.message)
    print()
    if result.capabilities:
        print("Capabilities:")
        for cap in result.capabilities:
            print(f"  - {cap}")
        print()
    if result.selected_titles:
        print("Strong fits:")
        for study in result.studies:
            label = study.fit_label or "match"
            print(f"  • {study.title} [{label} {study.fit_score:.2f}]")
            if study.capability:
                print(f"      proves: {study.capability}")
        print()
    if result.gaps:
        print("Gaps:")
        for gap in result.gaps:
            print(f"  ! {gap.capability}")
            if gap.gap_reason:
                print(f"      {gap.gap_reason}")
        print()
    if result.prefetched_at:
        print(f"Saved to research cache at {result.prefetched_at}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Match case studies to an RFP")
    parser.add_argument("rfp_id", help="RFP id (e.g. rfp-jw-123 or local id)")
    parser.add_argument("--json", action="store_true", help="Print full JSON response")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.rfp_id, as_json=args.json)))


if __name__ == "__main__":
    main()
