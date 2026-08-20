"""Agency-wide retired staff roster.

Key Personas in the UI is how humans mark someone retired. Go/No-Go and
proposal agents read this file so they never assign those people as current staff.

Seeded with known retirements; Sonja can add/remove anyone from the Key Persons modal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_DATA_DIR = Path(settings.database_path).parent
_STORE_PATH = _DATA_DIR / "retired_staff.json"

# Always-on defaults until the UI store is saved.
_SEED: tuple[dict[str, str], ...] = (
    {"id": "ron-comer", "name": "Ron Comer"},
)


def _store_path() -> Path:
    return _STORE_PATH


def _normalize_id(person_id: str, name: str) -> str:
    raw = (person_id or "").strip().casefold().replace(" ", "-")
    if raw:
        return raw
    return " ".join((name or "").split()).casefold().replace(" ", "-")


def _load_raw() -> list[dict[str, str]]:
    path = _store_path()
    if not path.exists():
        return [dict(row) for row in _SEED]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("retired staff store unreadable (%s) — using seed", exc)
        return [dict(row) for row in _SEED]
    rows = data.get("retired") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [dict(row) for row in _SEED]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        pid = _normalize_id(str(row.get("id") or ""), name)
        if pid in seen:
            continue
        seen.add(pid)
        out.append({"id": pid, "name": name})
    return out


def _save_raw(rows: list[dict[str, str]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"retired": rows}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def retired_records() -> list[dict[str, str]]:
    return _load_raw()


def retired_names() -> tuple[str, ...]:
    names = [row["name"] for row in _load_raw() if row.get("name")]
    return tuple(names)


def is_retired_id(person_id: str) -> bool:
    key = _normalize_id(person_id, "")
    return any(row["id"] == key for row in _load_raw())


def is_retired_name(name: str) -> bool:
    folded = " ".join((name or "").split()).casefold()
    if not folded:
        return False
    return any(row["name"].casefold() == folded for row in _load_raw())


def set_retired(*, person_id: str, name: str, retired: bool) -> list[dict[str, str]]:
    """Mark a team member retired or active. Returns the updated retired list."""
    pid = _normalize_id(person_id, name)
    display = " ".join((name or "").split()) or pid
    current = _load_raw()
    if retired:
        if not any(row["id"] == pid for row in current):
            current.append({"id": pid, "name": display})
        else:
            current = [
                {"id": pid, "name": display} if row["id"] == pid else row
                for row in current
            ]
    else:
        current = [row for row in current if row["id"] != pid]
    _save_raw(current)
    return current
