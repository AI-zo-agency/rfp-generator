"""Read/write Agent 1–2 system prompts: Supabase row first, disk fallback.

Table: demo_rfp_agent_prompts (id='rfp-two-agents', agent1, agent2).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("rfp-two-agents-demo.prompts")

PROMPT_ROW_ID = "rfp-two-agents"
TABLE = "demo_rfp_agent_prompts"

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_FILES = {
    "agent1": PROMPTS_DIR / "agent1_opportunity_system.txt",
    "agent2": PROMPTS_DIR / "agent2_strategy_delivery_system.txt",
}


def _disk_prompt(key: str) -> str:
    path = PROMPT_FILES[key]
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt file: {path.name}")
    return path.read_text(encoding="utf-8").strip()


def _write_disk(key: str, body: str) -> None:
    PROMPT_FILES[key].write_text(body.strip() + "\n", encoding="utf-8")


def supabase_configured() -> bool:
    try:
        from app.services.supabase_db import use_supabase_db

        return bool(use_supabase_db())
    except Exception:  # noqa: BLE001
        return False


def _fetch_row() -> dict[str, Any] | None:
    from app.services.supabase_db import _get_client

    res = (
        _get_client()
        .table(TABLE)
        .select("id,agent1,agent2,updated_at")
        .eq("id", PROMPT_ROW_ID)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _upsert_row(*, agent1: str, agent2: str) -> dict[str, Any]:
    from app.services.supabase_db import _get_client

    payload = {
        "id": PROMPT_ROW_ID,
        "agent1": agent1.strip(),
        "agent2": agent2.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = (
        _get_client()
        .table(TABLE)
        .upsert(payload, on_conflict="id")
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else payload


def ensure_seeded() -> dict[str, str]:
    """Return both prompts; seed Supabase from disk when row empty/missing."""
    disk = {"agent1": _disk_prompt("agent1"), "agent2": _disk_prompt("agent2")}
    if not supabase_configured():
        return {**disk, "source": "disk"}

    try:
        row = _fetch_row()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_store fetch failed, using disk: %s", str(exc)[:200])
        return {**disk, "source": "disk"}

    if not row:
        try:
            _upsert_row(agent1=disk["agent1"], agent2=disk["agent2"])
            logger.info("Seeded %s from disk files", TABLE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt_store seed failed: %s", str(exc)[:200])
            return {**disk, "source": "disk"}
        return {**disk, "source": "supabase"}

    out = {
        "agent1": (row.get("agent1") or "").strip() or disk["agent1"],
        "agent2": (row.get("agent2") or "").strip() or disk["agent2"],
        "source": "supabase",
    }
    if not (row.get("agent1") or "").strip() or not (row.get("agent2") or "").strip():
        try:
            _upsert_row(agent1=out["agent1"], agent2=out["agent2"])
            logger.info("Backfilled empty prompt column(s) on %s", TABLE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt_store backfill failed: %s", str(exc)[:200])
    return out


def load_prompt(key: str) -> tuple[str, str]:
    """Return (body, source) for agent1|agent2."""
    if key not in PROMPT_FILES:
        raise KeyError(key)
    both = ensure_seeded()
    return both[key], str(both.get("source") or "disk")


def load_all() -> dict[str, str]:
    return ensure_seeded()


def save_prompts(*, agent1: str | None = None, agent2: str | None = None) -> dict[str, str]:
    """Update one or both prompts. Writes Supabase when configured; mirrors disk."""
    current = ensure_seeded()
    next_a1 = current["agent1"] if agent1 is None else agent1.strip()
    next_a2 = current["agent2"] if agent2 is None else agent2.strip()

    if agent1 is not None:
        _write_disk("agent1", next_a1)
    if agent2 is not None:
        _write_disk("agent2", next_a2)

    if not supabase_configured():
        return {"agent1": next_a1, "agent2": next_a2, "source": "disk"}

    try:
        _upsert_row(agent1=next_a1, agent2=next_a2)
        logger.info(
            "Prompts saved to Supabase (agent1=%s agent2=%s)",
            agent1 is not None,
            agent2 is not None,
        )
        return {"agent1": next_a1, "agent2": next_a2, "source": "supabase"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_store save to Supabase failed: %s", str(exc)[:200])
        return {"agent1": next_a1, "agent2": next_a2, "source": "disk"}
