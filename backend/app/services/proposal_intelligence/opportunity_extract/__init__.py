"""Approved Agent 1 opportunity extract (LangExtract + tools + validators)."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_agent_prompt(name: str) -> str:
    """Load baked Agent 1 / Agent 2 system prompt from package prompts/."""
    key = (name or "").strip().lower()
    mapping = {
        "agent1": "agent1_opportunity_system.txt",
        "opportunity": "agent1_opportunity_system.txt",
        "agent2": "agent2_strategy_delivery_system.txt",
        "strategy": "agent2_strategy_delivery_system.txt",
    }
    fname = mapping.get(key)
    if not fname:
        raise ValueError(f"Unknown agent prompt: {name!r}")
    path = _PROMPTS_DIR / fname
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Empty prompt file: {path}")
    return text


def rfp_doc_from_plan_context(
    *,
    rfp_id: str,
    rfp_context: str,
):
    """Prefer real PDF pages; fall back to chunked rfp_context text."""
    from app.services.proposal_intelligence.opportunity_extract.agent1_tools import (
        RfpDoc,
    )

    rid = (rfp_id or "").strip()
    if rid:
        try:
            from app.services.rfp_repository import get_rfp_pdf_path
            from app.services.rfp_storage import load_rfp_pdf_bytes

            pdf_path = get_rfp_pdf_path(rid)
            pdf_bytes = load_rfp_pdf_bytes(rid, pdf_path)
            if pdf_bytes and pdf_bytes.startswith(b"%PDF"):
                return RfpDoc.from_pdf_bytes(pdf_bytes, filename=f"{rid}.pdf")
        except Exception:  # noqa: BLE001
            pass
    return RfpDoc.from_context_text(rfp_context or "", filename=f"{rid or 'rfp'}.txt")


# Lazy re-exports so callers can `from …opportunity_extract import RfpDoc` without
# importing merged_passes at package-import time (agent1_tools → merged_passes).
def __getattr__(name: str):
    if name in {
        "RfpDoc",
        "apply_opportunity_to_plan",
        "extract_opportunity_with_tools",
    }:
        from app.services.proposal_intelligence.opportunity_extract import agent1_tools

        return getattr(agent1_tools, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "RfpDoc",
    "apply_opportunity_to_plan",
    "extract_opportunity_with_tools",
    "load_agent_prompt",
    "rfp_doc_from_plan_context",
]
