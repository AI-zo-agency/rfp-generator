"""LangGraph Phase 2: Proposal Intelligence → ProposalExecutionPlan."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.proposal_intelligence.agents.budget_planner import run_budget_planner
from app.services.proposal_intelligence.agents.communication_planner import (
    run_communication_planner,
)
from app.services.proposal_intelligence.agents.compliance_mapping import run_compliance_mapping
from app.services.proposal_intelligence.agents.delivery_pattern import run_delivery_pattern
from app.services.proposal_intelligence.agents.dynamic_section_planner import (
    run_dynamic_section_planner,
)
from app.services.proposal_intelligence.agents.evaluation_criteria import run_evaluation_criteria
from app.services.proposal_intelligence.agents.methodology_planner import run_methodology_planner
from app.services.proposal_intelligence.agents.opportunity_strategy import run_opportunity_strategy
from app.services.proposal_intelligence.agents.qa_planner import run_qa_planner
from app.services.proposal_intelligence.agents.resource_planner import run_resource_planner
from app.services.proposal_intelligence.agents.retrieval_planner import run_retrieval_planner
from app.services.proposal_intelligence.agents.rfp_understanding import run_rfp_understanding
from app.services.proposal_intelligence.agents.risk_planner import run_risk_planner
from app.services.proposal_intelligence.agents.scope_analysis import run_scope_analysis
from app.services.proposal_intelligence.agents.section_strategy_planner import (
    run_section_strategy_planner,
)
from app.services.proposal_intelligence.agents.success_criteria import run_success_criteria
from app.services.proposal_intelligence.agents.timeline_planner import run_timeline_planner
from app.services.proposal_intelligence.agents.training_planner import run_training_planner
from app.services.proposal_intelligence.agents.validation import run_validate_plan
from app.services.proposal_intelligence.agents.work_breakdown_planner import (
    run_work_breakdown_planner,
)
from app.services.proposal_intelligence.agents.winning_pattern_intelligence import (
    run_winning_pattern_intelligence,
)
from app.services.proposal_intelligence.assembler import (
    derive_legacy_fields,
    refresh_proposal_memory,
    stamp_metadata,
)
from app.services.proposal_intelligence.log import get_intelligence_log_path, log_intel_event
from app.services.proposal_intelligence.plan_ops import IntelligenceError
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

logger = logging.getLogger(__name__)

_LLM_SEMAPHORE = asyncio.Semaphore(2)


class IntelligenceGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    plan: dict[str, Any]
    legacy: dict[str, Any]
    provider: str
    error: str | None


def _load_plan(state: IntelligenceGraphState) -> ProposalExecutionPlan:
    raw = state.get("plan") or {}
    if raw:
        return ProposalExecutionPlan.model_validate(raw)
    return ProposalExecutionPlan(rfpId=state.get("rfp_id") or "")


def _dump_plan(plan: ProposalExecutionPlan) -> dict[str, Any]:
    return plan.model_dump(by_alias=True)


def _meta(state: IntelligenceGraphState) -> dict[str, str]:
    return {
        "title": state.get("rfp_title") or "",
        "client": state.get("rfp_client") or "",
        "sector": state.get("rfp_sector") or "",
        "location": state.get("rfp_location") or "",
    }


async def _with_sem(coro):  # type: ignore[no-untyped-def]
    async with _LLM_SEMAPHORE:
        return await coro


def _wrap(name: str, fn):  # type: ignore[no-untyped-def]
    async def node(state: IntelligenceGraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        log_intel_event("node_enter", node=name, rfp_id=state.get("rfp_id"))
        plan = _load_plan(state)
        try:
            plan = await _with_sem(
                fn(
                    plan=plan,
                    rfp_context=state.get("rfp_context") or "",
                    rfp_meta=_meta(state),
                )
            )
        except TypeError:
            # Agents that don't take rfp_context
            try:
                plan = await _with_sem(fn(plan=plan, rfp_meta=_meta(state)))
            except TypeError:
                plan = await _with_sem(fn(plan=plan))
        except IntelligenceError as exc:
            log_intel_event("node_fail", node=name, error=str(exc)[:200])
            return {"error": str(exc), "plan": _dump_plan(plan)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Intelligence node %s failed (non-fatal): %s", name, exc)
            log_intel_event("node_warn", node=name, error=str(exc)[:200])
        log_intel_event("node_exit", node=name)
        return {
            "plan": _dump_plan(plan),
            "provider": plan.metadata.provider or state.get("provider") or "",
        }

    return node


async def _delivery_parallel(state: IntelligenceGraphState) -> dict[str, Any]:
    """Fan-out independent delivery planners under a shared semaphore."""
    if state.get("error"):
        return {}
    log_intel_event("node_enter", node="delivery_parallel")
    plan = _load_plan(state)
    meta = _meta(state)

    async def _one(runner):  # type: ignore[no-untyped-def]
        async with _LLM_SEMAPHORE:
            try:
                return await runner(plan=plan, rfp_meta=meta)
            except Exception as exc:  # noqa: BLE001
                logger.warning("delivery parallel agent failed: %s", exc)
                return plan

    # Run sequentially under semaphore via gather of locked coroutines —
    # each updates different delivery branches on the shared plan object.
    await asyncio.gather(
        _one(run_methodology_planner),
        _one(run_budget_planner),
        _one(run_risk_planner),
        _one(run_qa_planner),
        _one(run_communication_planner),
        _one(run_training_planner),
    )
    log_intel_event("node_exit", node="delivery_parallel")
    return {"plan": _dump_plan(plan), "provider": plan.metadata.provider or ""}


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
    legacy = derive_legacy_fields(plan)
    sections = legacy.get("rfpSections") or []
    log_intel_event(
        "legacy_derived",
        sections=len(sections),
        queries=len(legacy.get("sectionQueries") or {}),
    )
    # Terminal + intelligence log: what the RFP requires us to write
    logger = logging.getLogger(__name__)
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

    graph.add_node("rfp_understanding", _wrap("rfp_understanding", run_rfp_understanding))
    graph.add_node("compliance_mapping", _wrap("compliance_mapping", run_compliance_mapping))
    graph.add_node("scope_analysis", _wrap("scope_analysis", run_scope_analysis))
    graph.add_node("evaluation_criteria", _wrap("evaluation_criteria", run_evaluation_criteria))
    graph.add_node("success_criteria", _wrap("success_criteria", run_success_criteria))
    graph.add_node("opportunity_strategy", _wrap("opportunity_strategy", run_opportunity_strategy))
    graph.add_node("delivery_pattern", _wrap("delivery_pattern", run_delivery_pattern))
    graph.add_node("delivery_parallel", _delivery_parallel)
    graph.add_node(
        "work_breakdown", _wrap("work_breakdown", run_work_breakdown_planner)
    )
    graph.add_node("timeline", _wrap("timeline", run_timeline_planner))
    graph.add_node("resource", _wrap("resource", run_resource_planner))
    graph.add_node(
        "dynamic_section", _wrap("dynamic_section", run_dynamic_section_planner)
    )
    graph.add_node(
        "winning_pattern", _wrap("winning_pattern", run_winning_pattern_intelligence)
    )
    graph.add_node(
        "section_strategy", _wrap("section_strategy", run_section_strategy_planner)
    )
    graph.add_node("retrieval_planner", _wrap("retrieval_planner", run_retrieval_planner))
    graph.add_node("assemble", _assemble)
    graph.add_node("validate", _validate)
    graph.add_node("derive_legacy", _derive_legacy)

    graph.add_edge(START, "rfp_understanding")
    graph.add_edge("rfp_understanding", "compliance_mapping")
    graph.add_edge("compliance_mapping", "scope_analysis")
    graph.add_edge("scope_analysis", "evaluation_criteria")
    graph.add_edge("evaluation_criteria", "success_criteria")
    graph.add_edge("success_criteria", "opportunity_strategy")
    graph.add_edge("opportunity_strategy", "delivery_pattern")
    graph.add_edge("delivery_pattern", "delivery_parallel")
    graph.add_edge("delivery_parallel", "work_breakdown")
    graph.add_edge("work_breakdown", "timeline")
    graph.add_edge("timeline", "resource")
    graph.add_edge("resource", "dynamic_section")
    graph.add_edge("dynamic_section", "winning_pattern")
    graph.add_edge("winning_pattern", "section_strategy")
    graph.add_edge("section_strategy", "retrieval_planner")
    graph.add_edge("retrieval_planner", "assemble")
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
) -> tuple[ProposalExecutionPlan, dict[str, Any]]:
    """Run Phase 2 intelligence. Returns (plan, legacy_fields)."""
    log_path = get_intelligence_log_path()
    log_intel_event("graph_start", rfp_id=rfp_id, log_path=str(log_path))

    initial: IntelligenceGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "plan": ProposalExecutionPlan(rfpId=rfp_id).model_dump(by_alias=True),
        "legacy": {},
    }

    final = await _INTELLIGENCE_GRAPH.ainvoke(initial)
    if final.get("error"):
        raise IntelligenceError(str(final["error"]))

    plan = ProposalExecutionPlan.model_validate(final.get("plan") or {})
    legacy = final.get("legacy") or derive_legacy_fields(plan)
    log_intel_event(
        "graph_end",
        rfp_id=rfp_id,
        readiness=plan.validation.readiness_status,
        decisions=len(plan.decision_log),
    )
    return plan, legacy
