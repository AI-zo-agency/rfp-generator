"""LangGraph Phase 2: Proposal Intelligence → ProposalExecutionPlan.

Production graph uses batched agent passes (5 LLM hops) instead of ~18 sequential
specialist calls. Individual agent modules remain for tests and fallbacks.
The outline planner stays a dedicated hop — that prompt is accuracy-critical.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

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


class IntelligenceGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    page_limit: int | None
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
    from app.services.rfp_page_limit import resolve_page_limit

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


def _wrap(name: str, fn):  # type: ignore[no-untyped-def]
    async def node(state: IntelligenceGraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        from app.services.llm_call_context import llm_call_context

        log_intel_event("node_enter", node=name, rfp_id=state.get("rfp_id"))
        plan = _load_plan(state)
        try:
            with llm_call_context(
                rfp_id=str(state.get("rfp_id") or ""),
                node_name=name,
            ):
                plan = await fn(
                    plan=plan,
                    rfp_context=state.get("rfp_context") or "",
                    rfp_meta=_meta(state),
                )
        except TypeError:
            try:
                with llm_call_context(
                    rfp_id=str(state.get("rfp_id") or ""),
                    node_name=name,
                ):
                    plan = await fn(plan=plan, rfp_meta=_meta(state))
            except TypeError:
                with llm_call_context(
                    rfp_id=str(state.get("rfp_id") or ""),
                    node_name=name,
                ):
                    plan = await fn(plan=plan)
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
    legacy = derive_legacy_fields(plan, page_limit=page_limit_int)
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
    graph.add_node("writing_briefs", _wrap("writing_briefs", run_writing_briefs))
    graph.add_node("assemble", _assemble)
    graph.add_node("validate", _validate)
    graph.add_node("derive_legacy", _derive_legacy)

    graph.add_edge(START, "opportunity_extract")
    graph.add_edge("opportunity_extract", "strategy_delivery")
    graph.add_edge("strategy_delivery", "execution_plan")
    graph.add_edge("execution_plan", "dynamic_section")
    graph.add_edge("dynamic_section", "writing_briefs")
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
        "page_limit": page_limit,
        "plan": ProposalExecutionPlan(rfpId=rfp_id).model_dump(by_alias=True),
        "legacy": {},
    }

    final = await _INTELLIGENCE_GRAPH.ainvoke(initial)
    if final.get("error"):
        raise IntelligenceError(str(final["error"]))

    plan = ProposalExecutionPlan.model_validate(final.get("plan") or {})
    legacy = final.get("legacy") or derive_legacy_fields(plan, page_limit=page_limit)
    log_intel_event(
        "graph_end",
        rfp_id=rfp_id,
        readiness=plan.validation.readiness_status,
        decisions=len(plan.decision_log),
    )
    return plan, legacy
