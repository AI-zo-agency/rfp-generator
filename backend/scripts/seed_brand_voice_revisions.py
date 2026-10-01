"""Seed brand_voice_revisions from the repo's branding files. Safe to run twice.

    cd backend && ../.venv/bin/python scripts/seed_brand_voice_revisions.py

Adds rev 6 and rev 7. If no default is set yet, rev 6 becomes the default, which
matches what production does today. Activate rev 7 later from the Brand Voice page.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import brand_voice_revisions as bvr  # noqa: E402

BRANDING = Path(__file__).resolve().parents[2] / "branding"
FILES = [
    ("rev 6", "ZO_BRAND_AND_WRITING_STANDARDS_REV6.md"),
    ("rev 7", "ZO_BRAND_AND_WRITING_STANDARDS_REV7.md"),
]


def main() -> None:
    if not bvr.enabled():
        raise SystemExit("Supabase is not configured; nothing to seed.")
    ids: dict[str, str] = {}
    for label, name in FILES:
        body = (BRANDING / name).read_text(encoding="utf-8")
        try:
            rev = bvr.add_revision(label=label, body=body, created_by="seed", notes=f"Seeded from branding/{name}")
            ids[label] = rev.id
            print(f"added {label}: {rev.id}")
        except bvr.DuplicateRevision as exc:
            ids[label] = exc.existing_id
            print(f"{label} already stored: {exc.existing_id}")
    if bvr.active_pointer_id() is None:
        bvr.set_active(ids["rev 6"], updated_by="seed")
        print("default set to rev 6")
    else:
        print("default already set; left alone")


if __name__ == "__main__":
    main()
