"""Brand voice standards revisions: append-only history and one global active default.

Storage is Supabase only. Without Supabase (local SQLite dev) the repo file
branding/ZO_BRAND_AND_WRITING_STANDARDS_REV6.md is the only revision ("builtin").
Revisions never change once written, so they are cached by id for the life of the
process. The active pointer is cached for 30 seconds so Celery workers follow a
change without a restart.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUILTIN_ID = "builtin"
_BUILTIN_PATH = Path(__file__).resolve().parents[3] / "branding" / "ZO_BRAND_AND_WRITING_STANDARDS_REV6.md"
_ACTIVE_TTL_S = 30.0
MAX_BODY_BYTES = 200_000
WORD_SECTIONS = {1, 2, 3, 4, 8, 10}  # writing rules; 5-7 and 9 are look and images

# Used only if the repo file is missing at runtime. Task 3 replaces this with the
# original fallback text moved verbatim out of proposal_brand_voice.py.
FALLBACK_BODY = "# zö Brand & Writing Standards\n\n## 1. Company name\nAlways: zö agency.\n\n## 2. Writing rules\nWrite plainly."


class RevisionError(ValueError):
    pass


class DuplicateRevision(RevisionError):
    def __init__(self, existing_id: str) -> None:
        super().__init__("This exact file is already stored as a revision.")
        self.existing_id = existing_id


@dataclass(frozen=True)
class Revision:
    id: str
    label: str
    body: str
    sha256: str
    notes: str = ""
    created_by: str = ""
    created_at: str = ""


_by_id: dict[str, Revision] = {}
_active: tuple[float, str] | None = None


def _reset_caches() -> None:
    global _active
    _by_id.clear()
    _active = None
    builtin.cache_clear()


def sha256_of(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _plain(line: str) -> str:
    return line.replace("*", "").strip()


def words_only(md: str) -> str:
    """Keep the writing rules of a standards file. Drop change notes and the look and image sections."""
    out: list[str] = []
    keep = True  # the title and preamble before the first "## " heading
    in_look = False
    for line in md.splitlines():
        if line.startswith("## "):
            parts = line[3:].split()
            head = parts[0].rstrip(".") if parts else ""
            keep = head.isdigit() and int(head) in WORD_SECTIONS
            in_look = False
        elif line.strip() == "---":
            continue
        if keep and _plain(line) == "Look":  # section 8 ends its Words half at the Look label
            in_look = True
        if keep and not in_look:
            out.append(line)
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def validate(label: str, body: str) -> str:
    """Return the cleaned label, or raise RevisionError."""
    label = (label or "").strip()
    if not 1 <= len(label) <= 40:
        raise RevisionError("Label must be 1 to 40 characters.")
    if not body.strip():
        raise RevisionError("The file is empty.")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise RevisionError("The file is larger than 200 KB.")
    heads = {
        line.split()[1].rstrip(".")
        for line in body.splitlines()
        if line.startswith("## ") and len(line.split()) > 1
    }
    if not {"1", "2"} <= heads:
        raise RevisionError('This does not look like a zö standards file: no "## 1." and "## 2." sections.')
    return label


def enabled() -> bool:
    from app.services.supabase_db import use_supabase_db

    return use_supabase_db()


def _db() -> Any:
    from app.services.supabase_db import _get_client

    return _get_client()


@lru_cache(maxsize=1)
def builtin() -> Revision:
    try:
        body = _BUILTIN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        body = ""
    body = body or FALLBACK_BODY
    return Revision(BUILTIN_ID, "rev 6", body, sha256_of(body), "Repo file", "repo", "")


def _from_row(row: dict[str, Any]) -> Revision:
    return Revision(
        id=str(row["id"]),
        label=row["label"],
        body=row["body"],
        sha256=row["sha256"],
        notes=row.get("notes") or "",
        created_by=row.get("created_by") or "",
        created_at=str(row.get("created_at") or ""),
    )


def get_revision(rev_id: str | None) -> Revision | None:
    if not rev_id:
        return None
    if rev_id == BUILTIN_ID:
        return builtin()
    if rev_id in _by_id:
        return _by_id[rev_id]
    if not enabled():
        return None
    rows = _db().table("brand_voice_revisions").select("*").eq("id", rev_id).limit(1).execute().data
    if not rows:
        return None
    rev = _by_id[rev_id] = _from_row(rows[0])
    return rev


def list_revisions() -> list[Revision]:
    """Newest first. The repo file alone when Supabase is not configured."""
    if not enabled():
        return [builtin()]
    rows = _db().table("brand_voice_revisions").select("*").order("created_at", desc=True).execute().data
    return [_by_id.setdefault(str(r["id"]), _from_row(r)) for r in rows]


def active_pointer_id() -> str | None:
    if not enabled():
        return None
    rows = _db().table("brand_voice_active").select("revision_id").limit(1).execute().data
    return str(rows[0]["revision_id"]) if rows else None


def active_revision() -> Revision:
    """The global default. Falls back to the repo file if nothing is set or the read fails."""
    global _active
    if not enabled():
        return builtin()
    now = time.monotonic()
    try:
        if _active is None or now - _active[0] >= _ACTIVE_TTL_S:
            pointer = active_pointer_id()
            _active = (now, pointer) if pointer else None
        rev = get_revision(_active[1]) if _active else None
        if rev:
            return rev
    except Exception as exc:  # noqa: BLE001 - never block a proposal on the pointer read
        logger.warning("brand voice active pointer read failed: %s", str(exc)[:200])
    return builtin()


def add_revision(*, label: str, body: str, created_by: str, notes: str = "") -> Revision:
    label = validate(label, body)
    sha = sha256_of(body)
    existing = _db().table("brand_voice_revisions").select("id").eq("sha256", sha).limit(1).execute().data
    if existing:
        raise DuplicateRevision(str(existing[0]["id"]))
    row = (
        _db()
        .table("brand_voice_revisions")
        .insert({"label": label, "body": body, "sha256": sha, "notes": notes.strip(), "created_by": created_by})
        .execute()
        .data[0]
    )
    rev = _by_id[str(row["id"])] = _from_row(row)
    return rev


def set_active(rev_id: str, *, updated_by: str) -> Revision:
    global _active
    rev = get_revision(rev_id)
    if rev is None or rev.id == BUILTIN_ID:
        raise RevisionError("Unknown revision.")
    _db().table("brand_voice_active").upsert(
        {
            "singleton": True,
            "revision_id": rev.id,
            "updated_by": updated_by,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="singleton",
    ).execute()
    _active = (time.monotonic(), rev.id)
    return rev
