"""Local outline checkpoints — JSON + optional PDF sidecar under checkpoints/."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("rfp-two-agents-demo.checkpoint")

DEMO_ROOT = Path(__file__).resolve().parent
CHECKPOINTS_DIR = DEMO_ROOT / "checkpoints"


def _ensure_dir() -> Path:
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    return CHECKPOINTS_DIR


def checkpoint_json_path(demo_id: str) -> Path:
    safe = "".join(c for c in demo_id if c.isalnum() or c in "-_")
    if not safe or safe != demo_id:
        raise ValueError(f"Invalid demo_id for checkpoint: {demo_id!r}")
    return _ensure_dir() / f"{safe}.json"


def checkpoint_pdf_path(demo_id: str) -> Path:
    return checkpoint_json_path(demo_id).with_suffix(".pdf")


def save_outline_checkpoint(
    *,
    demo_id: str,
    plan: dict[str, Any],
    rfp_text: str,
    rfp_meta: dict[str, str],
    sections: list[dict[str, Any]],
    section_count: int,
    run_id: str,
    pdf_bytes: bytes | None = None,
    pdf_filename: str | None = None,
    opportunity_raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist frozen outline so Generate can resume without re-planning."""
    path = checkpoint_json_path(demo_id)
    payload = {
        "demo_id": demo_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "rfp_meta": dict(rfp_meta or {}),
        "rfp_text": rfp_text or "",
        "plan": plan,
        "sections": sections,
        "section_count": int(section_count),
        "pdf_filename": pdf_filename or "",
        "has_pdf": bool(pdf_bytes),
        "opportunity_raw": opportunity_raw or {},
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if pdf_bytes:
        checkpoint_pdf_path(demo_id).write_bytes(pdf_bytes)
    logger.info(
        "Checkpoint saved demo_id=%s sections=%s path=%s",
        demo_id,
        section_count,
        path.name,
    )
    return {
        "demo_id": demo_id,
        "path": str(path),
        "section_count": section_count,
        "saved_at": payload["saved_at"],
        "has_pdf": payload["has_pdf"],
    }


def list_checkpoints() -> list[dict[str, Any]]:
    root = _ensure_dir()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skip bad checkpoint %s: %s", path.name, exc)
            continue
        meta = data.get("rfp_meta") or {}
        rows.append(
            {
                "demo_id": data.get("demo_id") or path.stem,
                "saved_at": data.get("saved_at") or "",
                "section_count": data.get("section_count")
                or len(data.get("sections") or []),
                "title": str(meta.get("title") or path.stem),
                "client": str(meta.get("client") or ""),
                "has_pdf": bool(data.get("has_pdf"))
                or checkpoint_pdf_path(path.stem).is_file(),
            }
        )
    return rows


def load_outline_checkpoint(demo_id: str) -> dict[str, Any]:
    path = checkpoint_json_path(demo_id)
    if not path.is_file():
        raise FileNotFoundError(f"No checkpoint for {demo_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    pdf_path = checkpoint_pdf_path(demo_id)
    pdf_bytes: bytes | None = None
    if pdf_path.is_file():
        pdf_bytes = pdf_path.read_bytes()
    return {
        "demo_id": str(data.get("demo_id") or demo_id),
        "run_id": str(data.get("run_id") or ""),
        "rfp_meta": dict(data.get("rfp_meta") or {}),
        "rfp_text": str(data.get("rfp_text") or ""),
        "plan": data.get("plan") or {},
        "sections": data.get("sections") or [],
        "section_count": int(data.get("section_count") or 0),
        "pdf_bytes": pdf_bytes,
        "pdf_filename": str(data.get("pdf_filename") or "rfp.pdf"),
        "opportunity_raw": data.get("opportunity_raw") or {},
        "saved_at": data.get("saved_at"),
    }
