"""Demo: gated Agent 1 → approve → Agent 2 (imports production merged_passes).

Run from this folder (repo venv + backend/.env):

  ../../.venv/bin/python server.py

Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncio
import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DEMO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DEMO_ROOT.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
PROMPTS_DIR = DEMO_ROOT / "prompts"
STATIC_DIR = DEMO_ROOT / "static"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Settings load backend/.env (OPENROUTER_API_KEY, SUPERMEMORY_*, etc.)
from app.services.llm import LlmError  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.llm_call_context import llm_call_context  # noqa: E402
from app.services.llm_call_log import get_rfp_cost_breakdown  # noqa: E402
from app.services.proposal_intelligence import merged_passes  # noqa: E402
from app.services.proposal_intelligence.plan_ops import IntelligenceError  # noqa: E402
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan  # noqa: E402
from app.services.proposal_intelligence.merged_passes import (  # noqa: E402
    run_strategy_delivery,
)

from agent1_tools import (  # noqa: E402
    RfpDoc,
    apply_opportunity_to_plan,
    extract_opportunity_with_tools,
)
from progress_bus import (  # noqa: E402
    AGENT1_STEPS,
    AGENT2_STEPS,
    ProgressBus,
    sse,
    sse_comment,
)
from prompt_store import (  # noqa: E402
    load_all as _load_all_prompts,
    load_prompt as _store_load_prompt,
    save_prompts as _store_save_prompts,
    supabase_configured as _prompts_supabase_configured,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rfp-two-agents-demo")

app = FastAPI(title="RFP two-agent demo")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ponytail: in-memory sessions; restart clears demos. Persist if multi-user needed.
_SESSIONS: dict[str, dict[str, Any]] = {}


def _load_prompt(key: str) -> str:
    try:
        body, source = _store_load_prompt(key)
    except FileNotFoundError as exc:
        raise HTTPException(500, str(exc)) from exc
    logger.debug("Loaded prompt %s from %s (%d chars)", key, source, len(body))
    return body


def _apply_prompts() -> None:
    """Re-read prompts (Supabase row or disk) so mid-call tweaks apply."""
    merged_passes._OPPORTUNITY_SYSTEM = _load_prompt("agent1")
    merged_passes._STRATEGY_DELIVERY_SYSTEM = _load_prompt("agent2")


def _cost_for(demo_id: str) -> dict[str, Any]:
    return get_rfp_cost_breakdown(demo_id)


def _session_or_404(demo_id: str) -> dict[str, Any]:
    sess = _SESSIONS.get(demo_id)
    if not sess:
        raise HTTPException(404, "Unknown demo_id — run Agent 1 first")
    return sess


class PromptUpdate(BaseModel):
    agent1: str | None = None
    agent2: str | None = None


class Agent2Body(BaseModel):
    demo_id: str
    approved: bool = Field(
        default=True,
        description="Must be true — gate after reviewing Agent 1 output",
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    # Agents 1–2 are quality-critical → llm_heavy_model (not openrouter_model).
    heavy = (settings.llm_heavy_model or settings.openrouter_model or "").strip()
    label = heavy
    if "sonnet-5" in heavy.lower() or "sonnet_5" in heavy.lower():
        label = "Claude Sonnet 5"
    elif "sonnet" in heavy.lower():
        label = heavy.split("/")[-1].replace("-", " ").title()
    lx_model = "google/gemini-2.5-flash"
    try:
        from langextract_harvest import langextract_model_id

        lx_model = langextract_model_id()
    except Exception:  # noqa: BLE001
        pass
    prompts = _load_all_prompts()
    return {
        "ok": True,
        "openrouter_configured": bool(settings.openrouter_api_key),
        "supermemory_configured": bool(settings.supermemory_api_key),
        "supabase_configured": _prompts_supabase_configured(),
        "prompts_source": prompts.get("source") or "disk",
        "model": label,
        "model_id": heavy,
        "langextract_model": lx_model,
        "openrouter_model": settings.openrouter_model,
        "llm_heavy_model": settings.llm_heavy_model or "",
        "env_file": str(BACKEND_ROOT / ".env"),
    }


@app.get("/api/prompts")
async def get_prompts() -> dict[str, str]:
    data = _load_all_prompts()
    return {
        "agent1": data["agent1"],
        "agent2": data["agent2"],
        "source": data.get("source") or "disk",
    }


@app.put("/api/prompts")
async def put_prompts(body: PromptUpdate) -> dict[str, str]:
    saved = _store_save_prompts(agent1=body.agent1, agent2=body.agent2)
    logger.info(
        "Prompts saved source=%s (agent1=%s agent2=%s)",
        saved.get("source"),
        body.agent1 is not None,
        body.agent2 is not None,
    )
    return {
        "agent1": saved["agent1"],
        "agent2": saved["agent2"],
        "source": saved.get("source") or "disk",
    }


@app.get("/api/progress/catalog")
async def progress_catalog() -> dict[str, Any]:
    return {"agent1": AGENT1_STEPS, "agent2": AGENT2_STEPS}


@app.post("/api/agent1")
async def agent1(
    title: str = Form(default="Demo RFP"),
    client: str = Form(default=""),
    sector: str = Form(default=""),
    location: str = Form(default=""),
    file: UploadFile = File(...),
    stream: str = Form(default="1"),
) -> Any:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload an RFP PDF")
    raw = await file.read()
    try:
        doc = RfpDoc.from_pdf_bytes(raw, filename=file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    demo_id = f"demo-{uuid.uuid4().hex[:12]}"
    run_id = str(uuid.uuid4())
    meta = {
        "title": title.strip() or "Demo RFP",
        "client": client.strip(),
        "sector": sector.strip(),
        "location": location.strip(),
    }
    system_prompt = _load_prompt("agent1")
    want_stream = stream.strip().lower() not in {"0", "false", "no"}

    async def _run(bus: ProgressBus | None) -> dict[str, Any]:
        plan = ProposalExecutionPlan(rfpId=demo_id)
        logger.info(
            "Agent 1 (budget A1) start demo_id=%s pages=%s",
            demo_id,
            doc.page_count,
        )
        if bus:
            await bus.emit(
                step="parse_pdf",
                label="Parse RFP PDF",
                status="done",
                detail=f"{doc.page_count} pages · {file.filename}",
            )
        with llm_call_context(rfp_id=demo_id, run_id=run_id, node_name="opportunity_extract"):
            opportunity, tool_trace, provenance = await extract_opportunity_with_tools(
                doc=doc,
                system_prompt=system_prompt,
                rfp_meta=meta,
                on_progress=bus,
            )
            if bus:
                await bus.emit(
                    step="apply_plan",
                    label="Apply to execution plan",
                    status="active",
                )
            plan = await apply_opportunity_to_plan(plan=plan, raw=opportunity)
            if bus:
                await bus.emit(
                    step="apply_plan",
                    label="Apply to execution plan",
                    status="done",
                )
        plan_dump = plan.model_dump(by_alias=True)
        out_opportunity = dict(opportunity)
        _SESSIONS[demo_id] = {
            "plan": plan_dump,
            "rfp_text": doc.full_text(),
            "rfp_meta": meta,
            "agent1_approved": False,
            "run_id": run_id,
            "opportunity_raw": out_opportunity,
            "tool_trace": tool_trace,
            "provenance": provenance,
        }
        cost = _cost_for(demo_id)
        logger.info(
            "Agent 1 done demo_id=%s cost_usd=%s tools=%s provenance=%s",
            demo_id,
            cost.get("total_cost_usd"),
            len(tool_trace),
            len(provenance),
        )
        return {
            "demo_id": demo_id,
            "agent": "opportunity_extract",
            "output": out_opportunity,
            "provenance": provenance,
            "plan": plan_dump,
            "cost": cost,
            "toolTrace": tool_trace,
            "decisions": plan_dump.get("decisionLog") or plan_dump.get("decision_log") or [],
        }

    if not want_stream:
        try:
            return await _run(None)
        except IntelligenceError as exc:
            logger.error("Agent 1 intelligence error: %s", exc)
            raise HTTPException(502, str(exc)) from exc
        except LlmError as exc:
            logger.error("Agent 1 LLM error: %s", exc)
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent 1 failed")
            raise HTTPException(502, f"Agent 1 failed: {exc}") from exc

    bus = ProgressBus()

    async def event_gen():
        yield sse({"type": "hello", "agent": "agent1", "demo_id": demo_id, "steps": AGENT1_STEPS})

        async def worker() -> None:
            try:
                result = await _run(bus)
                await bus.result(result)
            except IntelligenceError as exc:
                logger.error("Agent 1 intelligence error: %s", exc)
                await bus.error(str(exc))
            except LlmError as exc:
                logger.error("Agent 1 LLM error: %s", exc)
                await bus.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agent 1 failed")
                await bus.error(f"Agent 1 failed: {exc}")
            finally:
                await bus.close()

        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(bus.q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield sse_comment("heartbeat")
                    if task.done():
                        break
                    continue
                if item is None:
                    break
                yield sse(item)
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            yield sse({"type": "done"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/approve")
async def approve(body: Agent2Body) -> dict[str, Any]:
    sess = _session_or_404(body.demo_id)
    if not body.approved:
        raise HTTPException(400, "Set approved=true after reviewing Agent 1")
    sess["agent1_approved"] = True
    logger.info("Agent 1 approved demo_id=%s", body.demo_id)
    return {"demo_id": body.demo_id, "approved": True}


class Agent2StreamBody(Agent2Body):
    stream: bool = True


@app.post("/api/agent2")
async def agent2(body: Agent2StreamBody) -> Any:
    sess = _session_or_404(body.demo_id)
    if not sess.get("agent1_approved") and not body.approved:
        raise HTTPException(400, "Approve Agent 1 output before running Agent 2")
    sess["agent1_approved"] = True

    async def _run(bus: ProgressBus | None) -> dict[str, Any]:
        plan = ProposalExecutionPlan.model_validate(sess["plan"])
        meta = sess["rfp_meta"]
        _apply_prompts()
        logger.info("Agent 2 start demo_id=%s", body.demo_id)
        if bus:
            await bus.emit(step="load_prompt", label="Load strategy prompt", status="done")
            await bus.emit(
                step="kb_retrieve",
                label="Supermemory KB retrieval",
                status="active",
                detail="won patterns · methodology · pricing · playbooks",
            )
            await bus.emit(
                step="strategy_llm",
                label="Sonnet · strategy & delivery",
                status="active",
            )
        with llm_call_context(
            rfp_id=body.demo_id,
            run_id=sess.get("run_id") or str(uuid.uuid4()),
            node_name="strategy_delivery",
        ):
            plan = await run_strategy_delivery(plan=plan, rfp_meta=meta)
        if bus:
            await bus.emit(step="kb_retrieve", label="Supermemory KB retrieval", status="done")
            await bus.emit(step="strategy_llm", label="Sonnet · strategy & delivery", status="done")
            await bus.emit(step="assemble", label="Assemble strategy + delivery JSON", status="active")
        plan_dump = plan.model_dump(by_alias=True)
        sess["plan"] = plan_dump
        cost = _cost_for(body.demo_id)
        if bus:
            await bus.emit(step="assemble", label="Assemble strategy + delivery JSON", status="done")
        logger.info(
            "Agent 2 done demo_id=%s cost_usd=%s",
            body.demo_id,
            cost.get("total_cost_usd"),
        )
        return {
            "demo_id": body.demo_id,
            "agent": "strategy_delivery",
            "output": {
                "strategy": (plan_dump.get("opportunity") or {}).get("strategy"),
                "delivery": plan_dump.get("delivery"),
            },
            "plan": plan_dump,
            "cost": cost,
            "decisions": plan_dump.get("decisionLog") or plan_dump.get("decision_log") or [],
        }

    if not body.stream:
        try:
            return await _run(None)
        except IntelligenceError as exc:
            raise HTTPException(502, str(exc)) from exc

    bus = ProgressBus()

    async def event_gen():
        yield sse(
            {
                "type": "hello",
                "agent": "agent2",
                "demo_id": body.demo_id,
                "steps": AGENT2_STEPS,
            }
        )

        async def worker() -> None:
            try:
                result = await _run(bus)
                await bus.result(result)
            except IntelligenceError as exc:
                await bus.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agent 2 failed")
                await bus.error(str(exc))
            finally:
                await bus.close()

        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(bus.q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield sse_comment("heartbeat")
                    if task.done():
                        break
                    continue
                if item is None:
                    break
                yield sse(item)
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            yield sse({"type": "done"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/cost/{demo_id}")
async def cost(demo_id: str) -> dict[str, Any]:
    return _cost_for(demo_id)


if __name__ == "__main__":
    import uvicorn

    port = 8765
    print(f"RFP two-agent demo → http://127.0.0.1:{port}")
    print(f"Env: {BACKEND_ROOT / '.env'}")
    print(f"Prompts: {PROMPTS_DIR}")
    print("Reload: on (demo + backend/app). In-memory sessions clear on reload.")
    # String import required for reload; watch demo tools + production modules.
    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        reload=True,
        reload_dirs=[str(DEMO_ROOT), str(BACKEND_ROOT / "app")],
    )
