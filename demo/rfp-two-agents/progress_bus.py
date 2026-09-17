"""SSE progress helpers for the two-agent demo."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

ProgressFn = Callable[..., Awaitable[None] | None]


def sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


def sse_comment(text: str = "ping") -> str:
    return f": {text}\n\n"


class ProgressBus:
    """Queue of progress events for one agent run."""

    def __init__(self) -> None:
        self.q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()

    async def emit(
        self,
        *,
        step: str,
        label: str,
        status: str = "active",
        detail: str = "",
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        await self.q.put(
            {
                "type": "step",
                "step": step,
                "label": label,
                "status": status,  # pending | active | done | error | skipped
                "detail": detail,
                "index": index,
                "total": total,
            }
        )

    def emit_threadsafe(
        self,
        *,
        step: str,
        label: str,
        status: str = "active",
        detail: str = "",
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        asyncio.run_coroutine_threadsafe(
            self.emit(
                step=step,
                label=label,
                status=status,
                detail=detail,
                index=index,
                total=total,
            ),
            self._loop,
        )

    async def result(self, payload: dict[str, Any]) -> None:
        await self.q.put({"type": "result", **payload})

    async def error(self, message: str) -> None:
        await self.q.put({"type": "error", "message": message})

    async def close(self) -> None:
        await self.q.put(None)

    def sync_callback(self) -> Callable[..., None]:
        """For LangExtract (runs in a worker thread)."""

        def _cb(
            step: str,
            label: str,
            status: str = "active",
            detail: str = "",
            index: int | None = None,
            total: int | None = None,
        ) -> None:
            self.emit_threadsafe(
                step=step,
                label=label,
                status=status,
                detail=detail,
                index=index,
                total=total,
            )

        return _cb


# Known Agent 1 milestones (UI renders these; server fills status).
AGENT1_STEPS: list[dict[str, str]] = [
    {"step": "parse_pdf", "label": "Parse RFP PDF"},
    {"step": "evidence_pack", "label": "Build evidence pack"},
    {"step": "lx_compliance", "label": "LangExtract · compliance"},
    {"step": "lx_evaluation", "label": "LangExtract · evaluation"},
    {"step": "lx_facts", "label": "LangExtract · dates & money"},
    {"step": "lx_scope", "label": "LangExtract · statement of work"},
    {"step": "sonnet_normalize", "label": "Sonnet · normalize opportunity"},
    {"step": "validators", "label": "Deterministic validators"},
    {"step": "repair", "label": "Targeted repair (if needed)"},
    {"step": "provenance", "label": "Ground provenance"},
    {"step": "apply_plan", "label": "Apply to execution plan"},
]

AGENT2_STEPS: list[dict[str, str]] = [
    {"step": "load_prompt", "label": "Load strategy prompt"},
    {"step": "kb_retrieve", "label": "Supermemory KB retrieval"},
    {"step": "strategy_llm", "label": "Sonnet · strategy & delivery"},
    {"step": "assemble", "label": "Assemble strategy + delivery JSON"},
]

OUTLINE_STEPS: list[dict[str, str]] = [
    {"step": "execution_plan", "label": "Execution plan · WBS / timeline / resources"},
    {"step": "dynamic_section", "label": "Dynamic section planner · strict_rfp"},
    {"step": "checklister", "label": "Checklister · submission/closing completeness"},
    {"step": "extract_titles", "label": "Extract section titles"},
]

# Frozen outline → manuscript → Word (no re-planner / Align / Complete Scan).
GENERATE_STEPS: list[dict[str, str]] = [
    {"step": "seed_rfp", "label": "Seed RFP + PDF"},
    {"step": "writing_briefs", "label": "Writing briefs"},
    {"step": "persist_research", "label": "Persist research cache"},
    {"step": "phase3", "label": "Phase 3 · draft sections"},
    {"step": "phase3_5", "label": "Phase 3.5 · budget (cost-gated)"},
    {"step": "phase3_6", "label": "Phase 3.6 · senior editor"},
    {"step": "ready_export", "label": "Ready for Word export"},
]

# Single-run demo: all phases in one SSE catalog (step ids stay unique across phases).
PIPELINE_STEPS: list[dict[str, str]] = [
    *AGENT1_STEPS,
    *AGENT2_STEPS,
    *OUTLINE_STEPS,
]
