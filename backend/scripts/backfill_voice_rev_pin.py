"""Pin every existing proposal to the current default revision. Run once at rollout,
while rev 6 is still the default, so nothing changes voice silently later.

    cd backend && ../.venv/bin/python scripts/backfill_voice_rev_pin.py --dry-run
    cd backend && ../.venv/bin/python scripts/backfill_voice_rev_pin.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import brand_voice_revisions as bvr  # noqa: E402
from app.services.proposal_repository import get_proposal_draft, save_proposal_draft  # noqa: E402
from app.services.rfp_repository import list_rfps  # noqa: E402


def main(dry_run: bool) -> None:
    active = bvr.active_revision()
    if bvr.enabled() and active.id == bvr.BUILTIN_ID:
        raise SystemExit("No stored default revision yet. Run seed_brand_voice_revisions.py first, then retry.")
    print(f"default revision: {active.label} ({active.id})")
    pinned = skipped = 0
    for rfp in list_rfps():
        draft = get_proposal_draft(rfp.id)
        if draft is None or draft.voice_rev_id:
            skipped += 1
            continue
        pinned += 1
        if not dry_run:
            draft.voice_rev_id = active.id
            save_proposal_draft(draft)
    print(f"{'would pin' if dry_run else 'pinned'} {pinned}, left alone {skipped}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
