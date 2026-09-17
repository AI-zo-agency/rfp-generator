"""Demo: one-shot RFP → opportunity → strategy → strict section list.

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
from urllib.parse import quote, unquote, urlparse

import asyncio

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from datetime import date, datetime, timezone  # noqa: E402

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
from app.services.proposal_intelligence.agents.checklister import (  # noqa: E402
    run_proposal_checklister,
)
from app.services.proposal_intelligence.agents.dynamic_section_planner import (  # noqa: E402
    run_dynamic_section_planner,
)
from app.services.proposal_intelligence.merged_passes import (  # noqa: E402
    run_execution_plan,
    run_strategy_delivery,
)
from app.services.monthly_llm_budget import clear_monthly_budget_cache  # noqa: E402

from agent1_tools import (  # noqa: E402
    RfpDoc,
    apply_opportunity_to_plan,
    extract_opportunity_with_tools,
)
from checkpoint_store import (  # noqa: E402
    list_checkpoints,
    load_outline_checkpoint,
    save_outline_checkpoint,
)
from progress_bus import (  # noqa: E402
    AGENT1_STEPS,
    AGENT2_STEPS,
    GENERATE_STEPS,
    OUTLINE_STEPS,
    PIPELINE_STEPS,
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

_MAX_RFP_BYTES = 50 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rfp-two-agents-demo")

# Not "demo-*" — llm_call_guards blocks those prefixes from OpenRouter spend.
# Signed email satisfies llm_require_user_email_for_proposals for production LLM hops.
_DEMO_USER_EMAIL = "rfp-demo@zo.agency"

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
        raise HTTPException(404, "Unknown demo_id — run the pipeline first")
    return sess


def _filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    name = Path(path).name
    if name.lower().endswith(".pdf"):
        return name
    return "rfp.pdf"


async def _fetch_pdf_from_url(url: str) -> tuple[bytes, str]:
    """Download an RFP PDF from http(s). Returns (bytes, filename)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "RFP URL must be http(s)")
    try:
        async with httpx.AsyncClient(
            timeout=120.0,
            follow_redirects=True,
            headers={"User-Agent": "zo-agency-rfp-demo/1.0"},
        ) as client:
            resp = await client.get(url.strip())
    except httpx.HTTPError as exc:
        logger.error("RFP URL fetch failed url=%s err=%s", url, exc)
        raise HTTPException(400, f"Could not download RFP URL: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(400, f"RFP URL returned HTTP {resp.status_code}")
    raw = resp.content
    if len(raw) > _MAX_RFP_BYTES:
        raise HTTPException(400, "RFP PDF exceeds 50MB limit")
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    filename = _filename_from_url(url)
    cd = resp.headers.get("content-disposition") or ""
    if "filename=" in cd.lower():
        # ponytail: naive Content-Disposition parse; upgrade if buyers send RFC 5987
        part = cd.split("filename=", 1)[-1].strip().strip("\"'")
        if part.lower().endswith(".pdf"):
            filename = Path(part).name
    if not (filename.lower().endswith(".pdf") or "pdf" in ctype or raw[:4] == b"%PDF"):
        raise HTTPException(400, "URL did not return a PDF")
    if raw[:4] != b"%PDF":
        raise HTTPException(400, "Downloaded file is not a PDF")
    return raw, filename


def _count_outline_nodes(nodes: list[dict[str, Any]]) -> int:
    total = 0
    for n in nodes:
        total += 1
        kids = n.get("children") or []
        if isinstance(kids, list):
            total += _count_outline_nodes([k for k in kids if isinstance(k, dict)])
    return total


def _slim_outline_sections(plan_dump: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat outline → nested {id, title, evaluationWeight, children[]} for UI."""
    writing = plan_dump.get("writing") or {}
    outline = writing.get("proposalOutline") or writing.get("proposal_outline") or {}
    sections = outline.get("sections") or []
    by_id: dict[str, dict[str, Any]] = {}
    for s in sections:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        by_id[sid] = {
            "id": sid,
            "title": str(s.get("title") or ""),
            "evaluationWeight": s.get("evaluationWeight", s.get("evaluation_weight")),
            "order": int(s.get("order") or 0),
            "parentId": s.get("parentId") or s.get("parent_id"),
            "child_ids": [str(c) for c in (s.get("children") or []) if c],
            "children": [],
        }
    for node in by_id.values():
        for cid in node["child_ids"]:
            child = by_id.get(cid)
            if child is not None:
                node["children"].append(child)
        node["children"].sort(key=lambda x: int(x.get("order") or 0))
    child_ids = {c["id"] for n in by_id.values() for c in n["children"]}
    roots = [n for n in by_id.values() if n["id"] not in child_ids]
    roots.sort(key=lambda x: int(x.get("order") or 0))

    def clean(n: dict[str, Any]) -> dict[str, Any]:
        raw = by_id.get(n["id"], {})
        return {
            "id": n["id"],
            "title": n["title"],
            "evaluationWeight": n["evaluationWeight"],
            "submissionInstrument": raw.get("submissionInstrument")
            or raw.get("submission_instrument"),
            "children": [clean(c) for c in n["children"]],
        }

    return [clean(r) for r in roots]


def _stamp_outline_protect_from_cap(plan: ProposalExecutionPlan) -> int:
    """Freeze client-approved TOC tabs against Phase 3 lean/cap drops."""
    stamped = 0
    for sec in plan.writing.proposal_outline.sections:
        sec.protect_from_cap = True
        stamped += 1
    return stamped


def _plan_from_session(sess: dict[str, Any]) -> ProposalExecutionPlan:
    raw = sess.get("plan")
    if not raw:
        raise HTTPException(400, "Session has no frozen outline — run the pipeline first")
    return ProposalExecutionPlan.model_validate(raw)


async def _upsert_demo_rfp_row(sess: dict[str, Any], demo_id: str) -> None:
    """Persist RFP so production load_rfp_for_proposal / Phase 3 can run."""
    from app.models.rfp import RfpRecord
    from app.services.rfp_repository import (
        save_manual_pdf,
        update_rfp_pdf_path,
        upsert_rfp,
    )

    meta = sess.get("rfp_meta") or {}
    today = date.today().isoformat()
    rfp_text = str(sess.get("rfp_text") or "")
    if len(rfp_text.strip()) < 200:
        raise HTTPException(400, "Session RFP text too short to draft a proposal")

    record = RfpRecord(
        id=demo_id,
        title=str(meta.get("title") or "Demo RFP"),
        client=str(meta.get("client") or "Demo Client") or "Demo Client",
        source="manual",
        sector=str(meta.get("sector") or "Public Sector") or "Public Sector",
        location=str(meta.get("location") or ""),
        dueDate=today,
        receivedDate=today,
        lastActivity=today,
        lastActivityNote="rfp-two-agents demo generate seed",
        goNoGo="go",
        stage="sections_4_5",
        status="in_progress",
        description=rfp_text,
    )
    await asyncio.to_thread(upsert_rfp, record)
    pdf_bytes = sess.get("pdf_bytes")
    if isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes:
        path = await asyncio.to_thread(save_manual_pdf, demo_id, bytes(pdf_bytes))
        await asyncio.to_thread(update_rfp_pdf_path, demo_id, path)
        logger.info("Demo RFP PDF saved demo_id=%s path=%s", demo_id, path)


async def _seed_demo_rfp_for_generate(
    sess: dict[str, Any],
    demo_id: str,
    *,
    bus: ProgressBus | None = None,
) -> ProposalExecutionPlan:
    """Upsert RFP, freeze outline, writing briefs, hard-validate, Phase 2 corpus.

    Does not re-run section planner / checklister — session plan is authoritative.
    Empty retrieval plan / blocked validation raises (production parity).
    """
    from app.services.proposal_common import ProposalError
    from app.services.proposal_generator import finalize_phase2_research_from_plan
    from app.services.proposal_intelligence.agents.validation import run_validate_plan
    from app.services.proposal_intelligence.merged_passes import run_writing_briefs

    total = len(GENERATE_STEPS)
    meta = dict(sess.get("rfp_meta") or {})
    run_id = str(sess.get("run_id") or uuid.uuid4())

    async def _step(step: str, label: str, index: int, status: str, detail: str = "") -> None:
        if not bus:
            return
        payload: dict[str, Any] = {
            "step": step,
            "label": label,
            "status": status,
            "index": index,
            "total": total,
        }
        if detail:
            payload["detail"] = detail
        await bus.emit(**payload)

    await _step("seed_rfp", "Seed RFP + PDF", 0, "active")
    await _upsert_demo_rfp_row(sess, demo_id)
    await _step("seed_rfp", "Seed RFP + PDF", 0, "done")

    plan = _plan_from_session(sess)
    stamped = _stamp_outline_protect_from_cap(plan)
    logger.info(
        "Demo outline frozen protectFromCap demo_id=%s sections=%s",
        demo_id,
        stamped,
    )

    await _step("writing_briefs", "Writing briefs + validate", 1, "active")
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="writing_briefs",
        user_email=_DEMO_USER_EMAIL,
    ):
        plan = await run_writing_briefs(plan=plan, rfp_meta=meta)
    plan = run_validate_plan(plan)
    if plan.validation.readiness_status == "blocked":
        blockers = list(plan.validation.blockers or [])
        raise ProposalError(
            "Plan not ready to draft: " + "; ".join(blockers[:5] or ["blocked"]),
            status_code=422,
        )
    if plan.validation.readiness_status != "ready":
        raise ProposalError(
            f"Plan validation status={plan.validation.readiness_status!r}; "
            "expected ready before Phase 2 corpus.",
            status_code=422,
        )
    await _step(
        "writing_briefs",
        "Writing briefs + validate",
        1,
        "done",
        detail=f"{len(plan.writing.section_plans.plans)} briefs",
    )

    await _step(
        "phase2_finalize",
        "Phase 2 · corpus + locks + lessons",
        2,
        "active",
    )
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="phase2_finalize",
        user_email=_DEMO_USER_EMAIL,
    ):
        research = await finalize_phase2_research_from_plan(
            demo_id,
            plan=plan,
            outline_mode="strict_rfp",
        )
    sess["plan"] = plan.model_dump(by_alias=True)
    sess["research_seeded"] = True
    corpus_n = len(research.evidence_corpus or [])
    await _step(
        "phase2_finalize",
        "Phase 2 · corpus + locks + lessons",
        2,
        "done",
        detail=f"{len(research.rfp_sections)} tabs · corpus={corpus_n}",
    )
    logger.info(
        "Demo Phase 2 finalize demo_id=%s sections=%s corpus=%s readiness=%s",
        demo_id,
        len(research.rfp_sections),
        corpus_n,
        plan.validation.readiness_status,
    )
    return plan


async def _sse_agent_stream(
    *,
    agent: str,
    demo_id: str,
    steps: list[dict[str, str]],
    run_fn,
) -> StreamingResponse:
    bus = ProgressBus()

    async def event_gen():
        yield sse({"type": "hello", "agent": agent, "demo_id": demo_id, "steps": steps})

        async def worker() -> None:
            try:
                result = await run_fn(bus)
                await bus.result(result)
            except IntelligenceError as exc:
                await bus.error(str(exc))
            except LlmError as exc:
                await bus.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                # ProposalError and other typed failures still stream as error events.
                from app.services.proposal_common import ProposalError

                if isinstance(exc, ProposalError):
                    await bus.error(str(exc))
                else:
                    logger.exception("%s failed", agent)
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


class PromptUpdate(BaseModel):
    agent1: str | None = None
    agent2: str | None = None


class Agent2Body(BaseModel):
    demo_id: str
    stream: bool = True


class GenerateBody(BaseModel):
    demo_id: str
    stream: bool = True


class LoadCheckpointBody(BaseModel):
    demo_id: str


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
    return {
        "pipeline": PIPELINE_STEPS,
        "agent1": AGENT1_STEPS,
        "agent2": AGENT2_STEPS,
        "outline": OUTLINE_STEPS,
        "generate": GENERATE_STEPS,
    }


@app.get("/api/checkpoints")
async def checkpoints_list() -> dict[str, Any]:
    rows = list_checkpoints()
    return {"checkpoints": rows, "count": len(rows)}


@app.get("/api/sessions")
async def sessions_list() -> dict[str, Any]:
    """In-memory demo sessions (survive page refresh; clear on server reload)."""
    rows: list[dict[str, Any]] = []
    for demo_id, sess in reversed(list(_SESSIONS.items())):
        plan = sess.get("plan") or {}
        sections = _slim_outline_sections(plan) if plan else []
        meta = sess.get("rfp_meta") or {}
        rows.append(
            {
                "demo_id": demo_id,
                "section_count": _count_outline_nodes(sections),
                "title": str(meta.get("title") or demo_id),
                "client": str(meta.get("client") or ""),
                "draft_ready": bool(sess.get("draft_ready")),
                "from_checkpoint": bool(sess.get("from_checkpoint")),
            }
        )
    return {"sessions": rows, "count": len(rows)}


@app.post("/api/sessions/load")
async def sessions_load(body: LoadCheckpointBody) -> dict[str, Any]:
    """Re-attach UI to an in-memory session after refresh (same outline, no re-run)."""
    sess = _session_or_404(body.demo_id)
    plan = sess.get("plan") or {}
    sections = _slim_outline_sections(plan)
    section_count = _count_outline_nodes(sections)
    writing = plan.get("writing") or {}
    return {
        "demo_id": body.demo_id,
        "draft_ready": bool(sess.get("draft_ready")),
        "output": {
            "outlineMode": "strict_rfp",
            "sections": sections,
            "sectionCount": section_count,
            "costRequirementStatus": writing.get("costRequirementStatus")
            or writing.get("cost_requirement_status"),
            "submissionConstraints": writing.get("submissionConstraints")
            or writing.get("submission_constraints")
            or [],
            "ambiguities": writing.get("ambiguities") or [],
        },
        "cost": _cost_for(body.demo_id),
    }


@app.post("/api/checkpoints/load")
async def checkpoints_load(body: LoadCheckpointBody) -> dict[str, Any]:
    """Restore a saved outline into the in-memory session (no re-planner)."""
    try:
        ckpt = load_outline_checkpoint(body.demo_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    demo_id = ckpt["demo_id"]
    plan = ckpt.get("plan") or {}
    if not plan:
        raise HTTPException(400, "Checkpoint has no execution plan")
    sections = ckpt.get("sections") or _slim_outline_sections(plan)
    section_count = int(ckpt.get("section_count") or _count_outline_nodes(sections))
    writing = plan.get("writing") or {}

    _SESSIONS[demo_id] = {
        "plan": plan,
        "rfp_text": ckpt.get("rfp_text") or "",
        "rfp_meta": ckpt.get("rfp_meta") or {},
        "run_id": ckpt.get("run_id") or str(uuid.uuid4()),
        "opportunity_raw": ckpt.get("opportunity_raw") or {},
        "tool_trace": [],
        "provenance": {},
        "pdf_bytes": ckpt.get("pdf_bytes"),
        "pdf_filename": ckpt.get("pdf_filename") or "rfp.pdf",
        "draft_ready": False,
        "research_seeded": False,
        "from_checkpoint": True,
    }
    logger.info(
        "Checkpoint loaded demo_id=%s sections=%s",
        demo_id,
        section_count,
    )
    return {
        "demo_id": demo_id,
        "from_checkpoint": True,
        "saved_at": ckpt.get("saved_at"),
        "output": {
            "outlineMode": "strict_rfp",
            "sections": sections,
            "sectionCount": section_count,
            "costRequirementStatus": writing.get("costRequirementStatus")
            or writing.get("cost_requirement_status"),
            "submissionConstraints": writing.get("submissionConstraints")
            or writing.get("submission_constraints")
            or [],
            "ambiguities": writing.get("ambiguities") or [],
        },
    }


async def _run_full_pipeline(
    *,
    doc: RfpDoc,
    filename: str,
    meta: dict[str, str],
    bus: ProgressBus | None,
    pdf_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Opportunity → strategy → strict outline. No human approve gates."""
    demo_id = f"rfpda-{uuid.uuid4().hex[:12]}"
    run_id = str(uuid.uuid4())
    system_prompt = _load_prompt("agent1")
    _apply_prompts()

    plan = ProposalExecutionPlan(rfpId=demo_id)
    logger.info(
        "Pipeline start demo_id=%s pages=%s file=%s",
        demo_id,
        doc.page_count,
        filename,
    )
    if bus:
        await bus.emit(
            step="parse_pdf",
            label="Parse RFP PDF",
            status="done",
            detail=f"{doc.page_count} pages · {filename}",
        )

    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="opportunity_extract",
        user_email=_DEMO_USER_EMAIL,
    ):
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
        rfp_id=demo_id,
        run_id=run_id,
        node_name="strategy_delivery",
        user_email=_DEMO_USER_EMAIL,
    ):
        plan = await run_strategy_delivery(plan=plan, rfp_meta=meta)
    if bus:
        await bus.emit(step="kb_retrieve", label="Supermemory KB retrieval", status="done")
        await bus.emit(step="strategy_llm", label="Sonnet · strategy & delivery", status="done")
        await bus.emit(step="assemble", label="Assemble strategy + delivery JSON", status="done")

    rfp_text = doc.full_text()
    if bus:
        await bus.emit(
            step="execution_plan",
            label="Execution plan · WBS / timeline / resources",
            status="active",
        )
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="execution_plan",
        user_email=_DEMO_USER_EMAIL,
    ):
        plan = await run_execution_plan(plan=plan, rfp_meta=meta)
    if bus:
        await bus.emit(
            step="execution_plan",
            label="Execution plan · WBS / timeline / resources",
            status="done",
        )
        await bus.emit(
            step="dynamic_section",
            label="Dynamic section planner · strict_rfp",
            status="active",
        )
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="dynamic_section",
        user_email=_DEMO_USER_EMAIL,
    ):
        plan = await run_dynamic_section_planner(
            plan=plan,
            rfp_context=rfp_text,
            rfp_meta=meta,
            outline_mode="strict_rfp",
        )
    if bus:
        await bus.emit(
            step="dynamic_section",
            label="Dynamic section planner · strict_rfp",
            status="done",
        )
        await bus.emit(
            step="checklister",
            label="Checklister · submission/closing completeness",
            status="active",
        )
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="checklister",
        user_email=_DEMO_USER_EMAIL,
    ):
        plan = await run_proposal_checklister(
            plan=plan,
            rfp_context=rfp_text,
            rfp_meta=meta,
        )
    if bus:
        await bus.emit(
            step="checklister",
            label="Checklister · submission/closing completeness",
            status="done",
        )
        await bus.emit(
            step="extract_titles",
            label="Extract section titles",
            status="active",
        )

    plan_dump = plan.model_dump(by_alias=True)
    sections = _slim_outline_sections(plan_dump)
    section_count = _count_outline_nodes(sections)
    _SESSIONS[demo_id] = {
        "plan": plan_dump,
        "rfp_text": rfp_text,
        "rfp_meta": meta,
        "run_id": run_id,
        "opportunity_raw": dict(opportunity),
        "tool_trace": tool_trace,
        "provenance": provenance,
        "pdf_bytes": pdf_bytes,
        "pdf_filename": filename,
        "draft_ready": False,
        "research_seeded": False,
    }
    try:
        save_outline_checkpoint(
            demo_id=demo_id,
            plan=plan_dump,
            rfp_text=rfp_text,
            rfp_meta=meta,
            sections=sections,
            section_count=section_count,
            run_id=run_id,
            pdf_bytes=pdf_bytes,
            pdf_filename=filename,
            opportunity_raw=dict(opportunity),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Checkpoint save failed demo_id=%s", demo_id)
    cost = _cost_for(demo_id)
    try:
        clear_monthly_budget_cache()
    except Exception:  # noqa: BLE001
        pass
    if bus:
        await bus.emit(
            step="extract_titles",
            label="Extract section titles",
            status="done",
            detail=f"{section_count} sections",
        )
    writing = plan_dump.get("writing") or {}
    logger.info(
        "Pipeline done demo_id=%s sections=%s cost_usd=%s",
        demo_id,
        section_count,
        cost.get("total_cost_usd"),
    )
    return {
        "demo_id": demo_id,
        "agent": "pipeline",
        "output": {
            "outlineMode": "strict_rfp",
            "sections": sections,
            "sectionCount": section_count,
            "costRequirementStatus": writing.get("costRequirementStatus")
            or writing.get("cost_requirement_status"),
            "submissionConstraints": writing.get("submissionConstraints")
            or writing.get("submission_constraints")
            or [],
            "ambiguities": writing.get("ambiguities") or [],
        },
        "opportunity": dict(opportunity),
        "strategy": {
            "strategy": (plan_dump.get("opportunity") or {}).get("strategy"),
            "delivery": plan_dump.get("delivery"),
        },
        "cost": cost,
        "toolTrace": tool_trace,
        "provenance": provenance,
    }


@app.post("/api/run")
async def run_pipeline(
    title: str = Form(default="Demo RFP"),
    client: str = Form(default=""),
    sector: str = Form(default=""),
    location: str = Form(default=""),
    rfp_url: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    stream: str = Form(default="1"),
) -> Any:
    """One-shot: PDF upload or URL → all agents → section list (SSE)."""
    url = (rfp_url or "").strip()
    has_file = bool(file and file.filename)
    if has_file and url:
        raise HTTPException(400, "Provide either an RFP PDF or a URL, not both")
    if not has_file and not url:
        raise HTTPException(400, "Upload an RFP PDF or paste an RFP URL")

    if has_file:
        assert file is not None
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Upload an RFP PDF")
        raw = await file.read()
        if len(raw) > _MAX_RFP_BYTES:
            raise HTTPException(400, "RFP PDF exceeds 50MB limit")
        filename = file.filename
    else:
        raw, filename = await _fetch_pdf_from_url(url)

    try:
        doc = RfpDoc.from_pdf_bytes(raw, filename=filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    meta = {
        "title": title.strip() or "Demo RFP",
        "client": client.strip(),
        "sector": sector.strip(),
        "location": location.strip(),
    }
    want_stream = stream.strip().lower() not in {"0", "false", "no"}

    async def _run(bus: ProgressBus | None) -> dict[str, Any]:
        return await _run_full_pipeline(
            doc=doc,
            filename=filename,
            meta=meta,
            bus=bus,
            pdf_bytes=raw,
        )

    if not want_stream:
        try:
            return await _run(None)
        except IntelligenceError as exc:
            logger.error("Pipeline intelligence error: %s", exc)
            raise HTTPException(502, str(exc)) from exc
        except LlmError as exc:
            logger.error("Pipeline LLM error: %s", exc)
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline failed")
            raise HTTPException(502, f"Pipeline failed: {exc}") from exc

    return await _sse_agent_stream(
        agent="pipeline",
        demo_id="pending",
        steps=PIPELINE_STEPS,
        run_fn=_run,
    )


async def _run_generate_proposal(
    *,
    demo_id: str,
    bus: ProgressBus | None,
) -> dict[str, Any]:
    """Frozen outline → Phase 2 corpus → 3 → 3.5 → closing → structure → 3.6 → P4 → finalize.

    Hard gates match production: empty retrieval / blocked validation → 422;
    budget ambiguity → 422 (absent cost still skips Phase 3.5).
    """
    from app.services.proposal_common import ProposalError
    from app.services.proposal_fulfill_rfp_gaps import run_build_finalize_pass
    from app.services.proposal_generator import (
        run_phase3_5_budget,
        run_phase3_6_self_edit,
        run_phase3_drafting,
        run_phase4_presubmit_review,
        run_post_budget_attach_passes,
    )
    from app.services.proposal_repository import (
        aget_proposal_draft,
        aget_research_cache,
    )
    from app.services.proposal_submission_authority import phase35_budget_gate

    sess = _session_or_404(demo_id)
    total = len(GENERATE_STEPS)
    run_id = str(sess.get("run_id") or uuid.uuid4())

    async def _step(
        step: str, label: str, index: int, status: str, detail: str = ""
    ) -> None:
        if not bus:
            return
        await bus.emit(
            step=step,
            label=label,
            status=status,
            detail=detail,
            index=index,
            total=total,
        )

    plan = await _seed_demo_rfp_for_generate(sess, demo_id, bus=bus)

    await _step("phase3", "Phase 3 · draft sections", 3, "active")
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="phase3_drafting",
        user_email=_DEMO_USER_EMAIL,
    ):
        draft, research = await run_phase3_drafting(demo_id)
    section_count = len(draft.sections) if draft else 0
    await _step(
        "phase3",
        "Phase 3 · draft sections",
        3,
        "done",
        detail=f"{section_count} sections",
    )

    budget_gate_status = "ran"
    budget_gate_detail = ""
    gate, gate_detail = phase35_budget_gate(plan)
    await _step("phase3_5", "Phase 3.5 · budget (cost-gated)", 4, "active")
    if gate == "skip":
        budget_gate_status = "skipped"
        budget_gate_detail = gate_detail or "No confirmed cost submittal"
        await _step(
            "phase3_5",
            "Phase 3.5 · budget (cost-gated)",
            4,
            "skipped",
            detail=budget_gate_detail,
        )
    elif gate == "block":
        raise ProposalError(
            gate_detail
            or "Budget generation blocked — unresolved RFP pricing ambiguity.",
            status_code=422,
        )
    else:
        with llm_call_context(
            rfp_id=demo_id,
            run_id=run_id,
            node_name="phase3_5_budget",
            user_email=_DEMO_USER_EMAIL,
        ):
            draft, research, _budget = await run_phase3_5_budget(demo_id)
        await _step("phase3_5", "Phase 3.5 · budget (cost-gated)", 4, "done")

    # Closing + submission + structure (same helper as generate_full_proposal).
    draft = await aget_proposal_draft(demo_id) or draft
    research = await aget_research_cache(demo_id) or research
    await _step("closing_submission", "Closing + submission attach", 5, "active")
    await _step("structure_coverage", "Structure coverage pass", 6, "active")
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="closing_submission",
        user_email=_DEMO_USER_EMAIL,
    ):
        draft, research = await run_post_budget_attach_passes(
            demo_id, draft, research
        )
    await _step("closing_submission", "Closing + submission attach", 5, "done")
    await _step(
        "structure_coverage",
        "Structure coverage pass",
        6,
        "done",
        detail="via post-budget attach",
    )

    await _step("phase3_6", "Phase 3.6 · senior editor", 7, "active")
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="phase3_6_self_edit",
        user_email=_DEMO_USER_EMAIL,
    ):
        draft, research, _edit = await run_phase3_6_self_edit(demo_id)
    await _step("phase3_6", "Phase 3.6 · senior editor", 7, "done")

    await _step(
        "phase4",
        "Phase 4 · pre-submit (+ adversarial)",
        8,
        "active",
    )
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="phase4_presubmit",
        user_email=_DEMO_USER_EMAIL,
    ):
        _review, research = await run_phase4_presubmit_review(demo_id)
    await _step(
        "phase4",
        "Phase 4 · pre-submit (+ adversarial)",
        8,
        "done",
    )

    await _step(
        "build_finalize",
        "Build finalize · final checks",
        9,
        "active",
    )
    with llm_call_context(
        rfp_id=demo_id,
        run_id=run_id,
        node_name="build_finalize",
        user_email=_DEMO_USER_EMAIL,
    ):
        await run_build_finalize_pass(demo_id)
    await _step(
        "build_finalize",
        "Build finalize · final checks",
        9,
        "done",
    )

    draft = await aget_proposal_draft(demo_id) or draft
    section_count = len(draft.sections) if draft else section_count
    await _step("ready_export", "Ready for Word export", 10, "done")

    sess["draft_ready"] = True
    writing = (sess.get("plan") or {}).get("writing") or {}
    cost = _cost_for(demo_id)
    try:
        clear_monthly_budget_cache()
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        "Generate done demo_id=%s sections=%s budget=%s cost_usd=%s",
        demo_id,
        section_count,
        budget_gate_status,
        cost.get("total_cost_usd"),
    )
    return {
        "demo_id": demo_id,
        "agent": "generate",
        "draft_ready": True,
        "sectionCount": section_count,
        "costRequirementStatus": writing.get("costRequirementStatus")
        or writing.get("cost_requirement_status")
        or plan.writing.cost_requirement_status,
        "budgetGate": {
            "status": budget_gate_status,
            "detail": budget_gate_detail,
        },
        "cost": cost,
    }


@app.post("/api/generate")
async def generate_proposal(body: GenerateBody) -> Any:
    """Draft full manuscript from frozen session outline (SSE)."""
    from app.services.proposal_common import ProposalError

    _session_or_404(body.demo_id)

    async def _run(bus: ProgressBus | None) -> dict[str, Any]:
        return await _run_generate_proposal(demo_id=body.demo_id, bus=bus)

    if not body.stream:
        try:
            return await _run(None)
        except ProposalError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except IntelligenceError as exc:
            raise HTTPException(502, str(exc)) from exc
        except LlmError as exc:
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Generate failed demo_id=%s", body.demo_id)
            raise HTTPException(502, f"Generate failed: {exc}") from exc

    return await _sse_agent_stream(
        agent="generate",
        demo_id=body.demo_id,
        steps=GENERATE_STEPS,
        run_fn=_run,
    )


@app.get("/api/export/docx")
@app.post("/api/export/docx")
async def export_docx(demo_id: str) -> Response:
    """Word export via production build_export_packets."""
    from app.services.proposal_docx_export import (
        ProposalDocxExportError,
        build_export_packets,
    )
    from app.services.proposal_repository import aget_proposal_draft

    sess = _session_or_404(demo_id)
    if not sess.get("draft_ready"):
        raise HTTPException(400, "Generate a proposal first before downloading Word")

    draft = await aget_proposal_draft(demo_id)
    if not draft or not draft.sections:
        raise HTTPException(400, "No proposal draft to export")

    meta = sess.get("rfp_meta") or {}
    title = str(meta.get("title") or "Proposal")
    rfp_text = str(sess.get("rfp_text") or "")
    try:
        packets = build_export_packets(
            draft=draft,
            rfp_title=title,
            rfp_text=rfp_text,
        )
    except ProposalDocxExportError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Docx export failed demo_id=%s", demo_id)
        raise HTTPException(502, f"Word export failed: {exc}") from exc

    if packets.mode == "separate_cost" and packets.zip_bytes and packets.zip_filename:
        encoded = quote(packets.zip_filename)
        return Response(
            content=packets.zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{encoded}"; filename*=UTF-8\'\'{encoded}'
                ),
                "X-Zo-Export-Mode": "separate_cost",
            },
        )

    file = packets.files[0]
    encoded = quote(file.filename)
    return Response(
        content=file.content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{encoded}"; filename*=UTF-8\'\'{encoded}'
            ),
            "X-Zo-Export-Mode": "single",
        },
    )


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

    demo_id = f"rfpda-{uuid.uuid4().hex[:12]}"
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
        with llm_call_context(
            rfp_id=demo_id,
            run_id=run_id,
            node_name="opportunity_extract",
            user_email=_DEMO_USER_EMAIL,
        ):
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


@app.post("/api/agent2")
async def agent2(body: Agent2Body) -> Any:
    sess = _session_or_404(body.demo_id)

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
            user_email=_DEMO_USER_EMAIL,
        ):
            plan = await run_strategy_delivery(plan=plan, rfp_meta=meta)
        if bus:
            await bus.emit(step="kb_retrieve", label="Supermemory KB retrieval", status="done")
            await bus.emit(step="strategy_llm", label="Sonnet · strategy & delivery", status="done")
            await bus.emit(step="assemble", label="Assemble strategy + delivery JSON", status="active")
        plan_dump = plan.model_dump(by_alias=True)
        sess["plan"] = plan_dump
        cost = _cost_for(body.demo_id)
        try:
            clear_monthly_budget_cache()
        except Exception:  # noqa: BLE001
            pass
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

    return await _sse_agent_stream(
        agent="agent2",
        demo_id=body.demo_id,
        steps=AGENT2_STEPS,
        run_fn=_run,
    )


class OutlineBody(BaseModel):
    demo_id: str
    stream: bool = True


@app.post("/api/outline")
async def outline(body: OutlineBody) -> Any:
    sess = _session_or_404(body.demo_id)
    if not sess.get("plan"):
        raise HTTPException(400, "No plan in session — run the pipeline first")

    async def _run(bus: ProgressBus | None) -> dict[str, Any]:
        plan = ProposalExecutionPlan.model_validate(sess["plan"])
        meta = dict(sess.get("rfp_meta") or {})
        rfp_text = str(sess.get("rfp_text") or "")
        if not rfp_text.strip():
            raise IntelligenceError("Session missing RFP text — re-run the pipeline")
        run_id = sess.get("run_id") or str(uuid.uuid4())
        logger.info("Outline start demo_id=%s mode=strict_rfp", body.demo_id)

        if bus:
            await bus.emit(
                step="execution_plan",
                label="Execution plan · WBS / timeline / resources",
                status="active",
            )
        with llm_call_context(
            rfp_id=body.demo_id,
            run_id=run_id,
            node_name="execution_plan",
            user_email=_DEMO_USER_EMAIL,
        ):
            plan = await run_execution_plan(plan=plan, rfp_meta=meta)
        if bus:
            await bus.emit(
                step="execution_plan",
                label="Execution plan · WBS / timeline / resources",
                status="done",
            )
            await bus.emit(
                step="dynamic_section",
                label="Dynamic section planner · strict_rfp",
                status="active",
            )
        with llm_call_context(
            rfp_id=body.demo_id,
            run_id=run_id,
            node_name="dynamic_section",
            user_email=_DEMO_USER_EMAIL,
        ):
            plan = await run_dynamic_section_planner(
                plan=plan,
                rfp_context=rfp_text,
                rfp_meta=meta,
                outline_mode="strict_rfp",
            )
        if bus:
            await bus.emit(
                step="dynamic_section",
                label="Dynamic section planner · strict_rfp",
                status="done",
            )
            await bus.emit(
                step="checklister",
                label="Checklister · submission/closing completeness",
                status="active",
            )
        with llm_call_context(
            rfp_id=body.demo_id,
            run_id=run_id,
            node_name="checklister",
            user_email=_DEMO_USER_EMAIL,
        ):
            plan = await run_proposal_checklister(
                plan=plan,
                rfp_context=rfp_text,
                rfp_meta=meta,
            )
        if bus:
            await bus.emit(
                step="checklister",
                label="Checklister · submission/closing completeness",
                status="done",
            )
            await bus.emit(
                step="extract_titles",
                label="Extract section titles",
                status="active",
            )

        plan_dump = plan.model_dump(by_alias=True)
        sess["plan"] = plan_dump
        sections = _slim_outline_sections(plan_dump)
        section_count = _count_outline_nodes(sections)
        cost = _cost_for(body.demo_id)
        try:
            clear_monthly_budget_cache()
        except Exception:  # noqa: BLE001
            pass
        if bus:
            await bus.emit(
                step="extract_titles",
                label="Extract section titles",
                status="done",
                detail=f"{section_count} sections",
            )
        logger.info(
            "Outline done demo_id=%s sections=%s cost_usd=%s",
            body.demo_id,
            section_count,
            cost.get("total_cost_usd"),
        )
        writing = plan_dump.get("writing") or {}
        return {
            "demo_id": body.demo_id,
            "agent": "outline",
            "output": {
                "outlineMode": "strict_rfp",
                "sections": sections,
                "sectionCount": section_count,
                "costRequirementStatus": writing.get("costRequirementStatus")
                or writing.get("cost_requirement_status"),
                "submissionConstraints": writing.get("submissionConstraints")
                or writing.get("submission_constraints")
                or [],
                "ambiguities": writing.get("ambiguities") or [],
            },
            "plan": plan_dump,
            "cost": cost,
            "decisions": plan_dump.get("decisionLog") or plan_dump.get("decision_log") or [],
        }

    if not body.stream:
        try:
            return await _run(None)
        except HTTPException:
            raise
        except IntelligenceError as exc:
            raise HTTPException(502, str(exc)) from exc
        except LlmError as exc:
            raise HTTPException(502, str(exc)) from exc

    return await _sse_agent_stream(
        agent="outline",
        demo_id=body.demo_id,
        steps=OUTLINE_STEPS,
        run_fn=_run,
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
