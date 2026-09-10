"""LangGraph Phase 2: Proposal Intelligence → ProposalExecutionPlan.

Production graph uses batched agent passes (5 LLM hops) instead of ~18 sequential
specialist calls. Individual agent modules remain for tests and fallbacks.
The outline planner stays a dedicated hop — that prompt is accuracy-critical.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.proposal_intelligence.agents.checklister import (
    run_proposal_checklister,
)
from app.services.proposal_intelligence.agents.dynamic_section_planner import (
    run_dynamic_section_planner,
)
from app.services.proposal_intelligence.agents.validation import run_validate_plan
from app.services.proposal_intelligence.assembler import (
    derive_legacy_fields,
    refresh_proposal_memory,
    stamp_metadata,
)
from app.services.proposal_intelligence.log import get_intelligence_log_path, log_intel_event
from app.services.proposal_intelligence.merged_passes import (
    run_execution_plan,
    run_opportunity_extract,
    run_strategy_delivery,
    run_writing_briefs,
)
from app.services.proposal_intelligence.plan_ops import IntelligenceError
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

logger = logging.getLogger(__name__)

# Human labels for the six LLM nodes, in graph order, so the BUILD MY PROPOSAL
# rail can light up Phase 2's internal steps one by one instead of showing a
# single "Intelligence" dot for the whole phase. The frontend mirrors this in
# INTELLIGENCE_STEP_LABELS (frontend/src/lib/proposal-pipeline-checkpoint.ts)
# — keep the two in sync.
INTELLIGENCE_NODE_LABELS: tuple[tuple[str, str], ...] = (
    ("opportunity_extract", "Reading the RFP opportunity"),
    ("strategy_delivery", "Shaping strategy & delivery"),
    ("execution_plan", "Building the execution plan"),
    ("dynamic_section", "Planning RFP section tabs"),
    ("checklister", "Auditing required sections"),
    ("writing_briefs", "Writing section briefs"),
)
_INTELLIGENCE_NODE_LABEL_MAP: dict[str, str] = dict(INTELLIGENCE_NODE_LABELS)
_INTELLIGENCE_NODE_STEP_INDEX: dict[str, int] = {
    name: idx for idx, (name, _label) in enumerate(INTELLIGENCE_NODE_LABELS, start=1)
}


class IntelligenceGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    page_limit: int | None
    outline_mode: str
    plan: dict[str, Any]
    legacy: dict[str, Any]
    provider: str
    error: str | None
    completed_nodes: list[str]


def _load_plan(state: IntelligenceGraphState) -> ProposalExecutionPlan:
    raw = state.get("plan") or {}
    if raw:
        return ProposalExecutionPlan.model_validate(raw)
    return ProposalExecutionPlan(rfpId=state.get("rfp_id") or "")


def _dump_plan(plan: ProposalExecutionPlan) -> dict[str, Any]:
    return plan.model_dump(by_alias=True)


def _meta(state: IntelligenceGraphState) -> dict[str, str]:
    from app.services.rfp_page_limit import remember_resolved_page_limit, resolve_page_limit

    rfp_id = state.get("rfp_id") or ""
    if rfp_id:
        page_limit = remember_resolved_page_limit(
            rfp_id,
            manual_page_limit=state.get("page_limit"),
            rfp_text=state.get("rfp_context") or "",
        )
    else:
        page_limit = resolve_page_limit(
            state.get("page_limit"),
            state.get("rfp_context") or "",
        )
    meta = {
        "title": state.get("rfp_title") or "",
        "client": state.get("rfp_client") or "",
        "sector": state.get("rfp_sector") or "",
        "location": state.get("rfp_location") or "",
    }
    if page_limit and page_limit > 0:
        meta["pageLimit"] = str(page_limit)
    return meta


def _context_fingerprint(rfp_context: str) -> str:
    """Hash the FULL rfp_context (never a prefix — a truncated key can collide
    across different RFP texts, silently resuming from a stale plan)."""
    return hashlib.sha256((rfp_context or "").encode("utf-8")).hexdigest()


async def _load_intelligence_checkpoint(
    rfp_id: str, fingerprint: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return (plan_dump, completed_nodes) for a resumable checkpoint, or
    (None, []) when there is none or it belongs to a different RFP text.

    Defensive by design: any checkpoint-store failure degrades to "no
    checkpoint" rather than breaking an otherwise-working phase.
    """
    try:
        from app.services.proposal_repository import aget_research_cache

        cache = await aget_research_cache(rfp_id)
        if cache is None or not cache.intelligence_checkpoint:
            return None, []
        checkpoint = cache.intelligence_checkpoint
        if checkpoint.get("fingerprint") != fingerprint:
            return None, []
        plan = checkpoint.get("plan") or None
        completed = list(checkpoint.get("completedNodes") or [])
        return plan, completed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load intelligence checkpoint for %s: %s", rfp_id, exc)
        return None, []


async def _save_intelligence_checkpoint(
    rfp_id: str,
    fingerprint: str,
    plan_dump: dict[str, Any],
    completed: list[str],
) -> None:
    """Persist a resumable checkpoint. Failures are logged and swallowed —
    a checkpoint-store failure must never break a phase that is otherwise
    working."""
    try:
        from app.services.proposal_repository import (
            aget_research_cache,
            asave_research_cache,
        )
        from app.models.proposal import ProposalResearchCache

        now = datetime.now(timezone.utc).isoformat()
        cache = await aget_research_cache(rfp_id)
        if cache is None:
            cache = ProposalResearchCache(rfp_id=rfp_id, updated_at=now)
        cache.intelligence_checkpoint = {
            "fingerprint": fingerprint,
            "completedNodes": list(completed),
            "plan": plan_dump,
            "updatedAt": now,
        }
        await asave_research_cache(cache)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save intelligence checkpoint for %s: %s", rfp_id, exc)


async def _clear_intelligence_checkpoint(rfp_id: str) -> None:
    """Clear a completed phase's checkpoint so the next fresh run redoes
    everything. Defensive: a failure here must not break a successful run."""
    try:
        from app.services.proposal_repository import (
            aget_research_cache,
            asave_research_cache,
        )

        cache = await aget_research_cache(rfp_id)
        if cache is None or not cache.intelligence_checkpoint:
            return
        cache.intelligence_checkpoint = None
        await asave_research_cache(cache)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to clear intelligence checkpoint for %s: %s", rfp_id, exc)


def _wrap(name: str, fn):  # type: ignore[no-untyped-def]
    async def node(state: IntelligenceGraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        if name in (state.get("completed_nodes") or []):
            log_intel_event("node_skip", node=name, reason="checkpoint")
            return {}
        from app.services.llm_call_context import llm_call_context

        log_intel_event("node_enter", node=name, rfp_id=state.get("rfp_id"))
        if name in _INTELLIGENCE_NODE_STEP_INDEX:
            try:
                from app.services.proposal_pipeline_checkpoint import (
                    record_pipeline_activity,
                )

                await record_pipeline_activity(
                    str(state.get("rfp_id") or ""),
                    label=_INTELLIGENCE_NODE_LABEL_MAP.get(name, name),
                    detail=None,
                    step_index=_INTELLIGENCE_NODE_STEP_INDEX[name],
                    step_total=len(INTELLIGENCE_NODE_LABELS),
                    in_progress_phase="phase-2",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to record intelligence progress for node %s: %s", name, exc
                )
        plan = _load_plan(state)
        # Build the kwargs from the agent's OWN signature instead of calling and
        # catching TypeError. The old try/except could not tell "this agent does
        # not take rfp_context" from "a TypeError was raised INSIDE the agent" —
        # so a real bug in an agent body (e.g. a wrong kwarg passed on to
        # safe_chat_json) was retried twice and then reported as a bogus
        # "missing 1 required keyword-only argument", hiding the true cause.
        import inspect

        accepted = inspect.signature(fn).parameters
        call_kwargs: dict[str, Any] = {"plan": plan}
        if "rfp_context" in accepted:
            call_kwargs["rfp_context"] = state.get("rfp_context") or ""
        if "rfp_meta" in accepted:
            call_kwargs["rfp_meta"] = _meta(state)
        if "outline_mode" in accepted:
            call_kwargs["outline_mode"] = state.get("outline_mode") or "zo_template"
        succeeded = False
        try:
            with llm_call_context(
                rfp_id=str(state.get("rfp_id") or ""),
                node_name=name,
            ):
                plan = await fn(**call_kwargs)
            succeeded = True
        except IntelligenceError as exc:
            log_intel_event("node_fail", node=name, error=str(exc)[:200])
            return {"error": str(exc), "plan": _dump_plan(plan)}
        except Exception as exc:  # noqa: BLE001
            # NOT a success — do not add `name` to completed_nodes below. A node
            # that silently failed has not produced its work, and marking it
            # done would bake the gap in permanently on resume.
            logger.warning("Intelligence node %s failed (non-fatal): %s", name, exc)
            log_intel_event("node_warn", node=name, error=str(exc)[:200])
        log_intel_event("node_exit", node=name)
        plan_dump = _dump_plan(plan)
        result: dict[str, Any] = {
            "plan": plan_dump,
            "provider": plan.metadata.provider or state.get("provider") or "",
        }
        if succeeded:
            # The graph is linear, so plain replace semantics (rather than a
            # LangGraph reducer) are correct here: each node's returned dict
            # fully supersedes the prior completed_nodes list with itself appended.
            completed = list(state.get("completed_nodes") or [])
            completed.append(name)
            result["completed_nodes"] = completed
            await _save_intelligence_checkpoint(
                str(state.get("rfp_id") or ""),
                _context_fingerprint(state.get("rfp_context") or ""),
                plan_dump,
                completed,
            )
        return result

    return node


async def _assemble(state: IntelligenceGraphState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    plan = _load_plan(state)
    plan = refresh_proposal_memory(plan)
    plan = stamp_metadata(
        plan, rfp_id=state.get("rfp_id") or "", provider=state.get("provider")
    )
    return {"plan": _dump_plan(plan)}


async def _validate(state: IntelligenceGraphState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    plan = run_validate_plan(_load_plan(state))
    return {"plan": _dump_plan(plan)}


async def _derive_legacy(state: IntelligenceGraphState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    plan = _load_plan(state)
    page_limit = state.get("page_limit")
    try:
        page_limit_int = int(page_limit) if page_limit else None
    except (TypeError, ValueError):
        page_limit_int = None
    legacy = derive_legacy_fields(
        plan,
        page_limit=page_limit_int,
        outline_mode=str(state.get("outline_mode") or "zo_template"),
    )
    sections = legacy.get("rfpSections") or []
    log_intel_event(
        "legacy_derived",
        sections=len(sections),
        queries=len(legacy.get("sectionQueries") or {}),
    )
    logger.info(
        "Phase 2 RFP outline for %s — %d required proposal sections:",
        state.get("rfp_id"),
        len(sections),
    )
    for index, section in enumerate(sections, 1):
        title = getattr(section, "title", None) or (
            section.get("title") if isinstance(section, dict) else None
        ) or "?"
        reqs = getattr(section, "requirements", None) or (
            section.get("requirements") if isinstance(section, dict) else None
        ) or []
        weight = getattr(section, "evaluation_weight", None)
        if weight is None and isinstance(section, dict):
            weight = section.get("evaluationWeight")
        weight_bit = f" weight={weight}" if weight is not None else ""
        logger.info("  %02d. %s%s (%d req bullets)", index, title, weight_bit, len(reqs))
        log_intel_event(
            "rfp_section_mapped",
            index=index,
            title=title,
            requirements=len(reqs),
            weight=weight,
        )
    return {"legacy": legacy, "plan": _dump_plan(plan)}


def _build_graph() -> Any:
    graph = StateGraph(IntelligenceGraphState)

    graph.add_node("opportunity_extract", _wrap("opportunity_extract", run_opportunity_extract))
    graph.add_node("strategy_delivery", _wrap("strategy_delivery", run_strategy_delivery))
    graph.add_node("execution_plan", _wrap("execution_plan", run_execution_plan))
    graph.add_node(
        "dynamic_section", _wrap("dynamic_section", run_dynamic_section_planner)
    )
    graph.add_node(
        "checklister", _wrap("checklister", run_proposal_checklister)
    )
    graph.add_node("writing_briefs", _wrap("writing_briefs", run_writing_briefs))
    graph.add_node("assemble", _assemble)
    graph.add_node("validate", _validate)
    graph.add_node("derive_legacy", _derive_legacy)

    graph.add_edge(START, "opportunity_extract")
    graph.add_edge("opportunity_extract", "strategy_delivery")
    graph.add_edge("strategy_delivery", "execution_plan")
    graph.add_edge("execution_plan", "dynamic_section")
    graph.add_edge("dynamic_section", "checklister")
    graph.add_edge("checklister", "writing_briefs")
    graph.add_edge("writing_briefs", "assemble")
    graph.add_edge("assemble", "validate")
    graph.add_edge("validate", "derive_legacy")
    graph.add_edge("derive_legacy", END)
    return graph.compile()


_INTELLIGENCE_GRAPH = _build_graph()


async def run_intelligence_graph(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    page_limit: int | None = None,
    outline_mode: str = "zo_template",
) -> tuple[ProposalExecutionPlan, dict[str, Any]]:
    """Run Phase 2 intelligence. Returns (plan, legacy_fields)."""
    log_path = get_intelligence_log_path()
    log_intel_event("graph_start", rfp_id=rfp_id, log_path=str(log_path))

    fingerprint = _context_fingerprint(rfp_context)
    checkpoint_plan, completed_nodes = await _load_intelligence_checkpoint(
        rfp_id, fingerprint
    )
    log_intel_event(
        "checkpoint_loaded",
        rfp_id=rfp_id,
        nodes_skipped=len(completed_nodes),
        completed=completed_nodes,
    )

    mode = (outline_mode or "zo_template").strip().lower()
    if mode not in {"zo_template", "strict_rfp"}:
        mode = "zo_template"

    initial: IntelligenceGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "page_limit": page_limit,
        "outline_mode": mode,
        "plan": checkpoint_plan or ProposalExecutionPlan(rfpId=rfp_id).model_dump(by_alias=True),
        "legacy": {},
        "completed_nodes": completed_nodes,
    }

    final = await _INTELLIGENCE_GRAPH.ainvoke(initial)
    if final.get("error"):
        raise IntelligenceError(str(final["error"]))

    plan = ProposalExecutionPlan.model_validate(final.get("plan") or {})
    legacy = final.get("legacy") or derive_legacy_fields(
        plan, page_limit=page_limit, outline_mode=mode
    )
    log_intel_event(
        "graph_end",
        rfp_id=rfp_id,
        readiness=plan.validation.readiness_status,
        decisions=len(plan.decision_log),
    )
    await _clear_intelligence_checkpoint(rfp_id)
    return plan, legacy
