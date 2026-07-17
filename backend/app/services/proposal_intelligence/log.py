"""Plain-text LangGraph plot for Phase 2 Proposal Intelligence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("app.proposal_intelligence")

LOGS_DIR = Path(__file__).resolve().parents[3] / "logs"
LANGGRAPH_INTEL_LOG_FILE = LOGS_DIR / "langgraph_intelligence.txt"

_file_handler_ready = False


def _ensure_file_handler() -> None:
    global _file_handler_ready
    if _file_handler_ready:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LANGGRAPH_INTEL_LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    for existing in logger.handlers:
        if (
            isinstance(existing, logging.FileHandler)
            and Path(existing.baseFilename) == LANGGRAPH_INTEL_LOG_FILE
        ):
            _file_handler_ready = True
            return
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    _file_handler_ready = True


def get_intelligence_log_path() -> Path:
    _ensure_file_handler()
    return LANGGRAPH_INTEL_LOG_FILE


def log_intel_event(event: str, **fields: Any) -> None:
    _ensure_file_handler()
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    extras = " ".join(f"{key}={value!r}" for key, value in fields.items() if value is not None)
    line = f"[{stamp}] {event}" + (f" | {extras}" if extras else "")
    logger.info(line)
    try:
        with LANGGRAPH_INTEL_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
