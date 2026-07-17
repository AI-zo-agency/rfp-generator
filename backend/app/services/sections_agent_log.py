"""CrewAI-style structured logging for Sections 1–3 LangGraph agents.

Also writes a plain-text plot of every LangGraph node to:
  backend/logs/langgraph_sections.txt
so you can open that file and follow the agent run without digging through uvicorn.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("app.sections_agents")

# Plain-text LangGraph plot — open this file while / after Draft Sections 1–3.
LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
LANGGRAPH_LOG_FILE = LOGS_DIR / "langgraph_sections.txt"

NodeHandler = Callable[..., Awaitable[dict[str, Any]]]

_file_handler_ready = False


def _ensure_file_handler() -> None:
    """Attach a FileHandler once so all agent banners land in the .txt plot."""
    global _file_handler_ready
    if _file_handler_ready:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LANGGRAPH_LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    # Avoid duplicate handlers on reload.
    for existing in logger.handlers:
        if isinstance(existing, logging.FileHandler) and Path(existing.baseFilename) == LANGGRAPH_LOG_FILE:
            _file_handler_ready = True
            return
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Don't bubble the same banners twice into root/uvicorn unless useful.
    logger.propagate = True
    _file_handler_ready = True


def get_langgraph_log_path() -> Path:
    _ensure_file_handler()
    return LANGGRAPH_LOG_FILE


def log_graph_event(message: str) -> None:
    """One-line graph-flow event (astream node finished, merge, etc.)."""
    _ensure_file_handler()
    logger.info(message)


@dataclass(frozen=True)
class AgentStepMeta:
    node_id: str
    agent_name: str
    layer: str
    action: str
    phase_cq: int
    phase_legacy: int
    total_cq: int = 11
    total_legacy: int = 5


AGENT_STEPS: dict[str, AgentStepMeta] = {
    "fetch_knowledge_base": AgentStepMeta(
        node_id="fetch_knowledge_base",
        agent_name="Knowledge Base Retriever",
        layer="Orchestration · Legacy",
        action="Bulk Supermemory search for voice, bios, case studies, company",
        phase_cq=0,
        phase_legacy=1,
    ),
    "fetch_proposal_context": AgentStepMeta(
        node_id="fetch_proposal_context",
        agent_name="Proposal Context Agent",
        layer="Company Qualification · Orchestrator",
        action="Classify RFP from excerpt only → ProposalContext JSON",
        phase_cq=1,
        phase_legacy=0,
    ),
    "fetch_company_truth": AgentStepMeta(
        node_id="fetch_company_truth",
        agent_name="Company Truth Agent",
        layer="Company Qualification · Orchestrator",
        action="JIT company-scoped retrieval → CompanyTruth JSON (no prose)",
        phase_cq=2,
        phase_legacy=0,
    ),
    "prioritize_capabilities": AgentStepMeta(
        node_id="prioritize_capabilities",
        agent_name="Capability Prioritization Agent",
        layer="Company Qualification · Orchestrator",
        action="Rank capabilities → Primary / Secondary / Omit JSON",
        phase_cq=3,
        phase_legacy=0,
    ),
    "plan_section_1": AgentStepMeta(
        node_id="plan_section_1",
        agent_name="Section 1 Agent",
        layer="Company Qualification · Section 1 track",
        action="Content budgets + inclusion plan → JSON (no prose)",
        phase_cq=4,
        phase_legacy=0,
    ),
    "build_section_1_cq": AgentStepMeta(
        node_id="build_section_1_cq",
        agent_name="Section 1 Builder",
        layer="Company Qualification · Section 1 track",
        action="Assemble Section 1 prose from plan + CompanyTruth",
        phase_cq=5,
        phase_legacy=0,
    ),
    "select_team": AgentStepMeta(
        node_id="select_team",
        agent_name="Team Selection Agent",
        layer="Company Qualification · Section 2 track",
        action="Roster JIT query + skill-based team pick → JSON",
        phase_cq=6,
        phase_legacy=0,
    ),
    "build_bios": AgentStepMeta(
        node_id="build_bios",
        agent_name="Bio Builder",
        layer="Company Qualification · Section 2 track",
        action="One 04_Bio fetch per selected person → bio subsections",
        phase_cq=7,
        phase_legacy=0,
    ),
    "select_evidence": AgentStepMeta(
        node_id="select_evidence",
        agent_name="Evidence Selection Agent",
        layer="Company Qualification · Section 3 track",
        action="Score candidate index (snippets only) → top 3–5 titles",
        phase_cq=8,
        phase_legacy=0,
    ),
    "build_case_studies": AgentStepMeta(
        node_id="build_case_studies",
        agent_name="Case Study Builder",
        layer="Company Qualification · Section 3 track",
        action="Full retrieval per selected study → case study subsections",
        phase_cq=9,
        phase_legacy=0,
    ),
    "join_sections": AgentStepMeta(
        node_id="join_sections",
        agent_name="Section Join",
        layer="Company Qualification · Orchestrator",
        action="Synchronize parallel S1 / S2 / S3 tracks",
        phase_cq=10,
        phase_legacy=0,
    ),
    "validate_sections_editorial": AgentStepMeta(
        node_id="validate_sections_editorial",
        agent_name="Editorial Validation Agent",
        layer="Company Qualification · QA",
        action="Review all Sections 1–3 → editorial recommendations JSON",
        phase_cq=11,
        phase_legacy=0,
    ),
    "synthesize_proposal_voice": AgentStepMeta(
        node_id="synthesize_proposal_voice",
        agent_name="Brand Voice Agent",
        layer="Orchestration · Shared",
        action="JIT voice KB + RFP tone adaptation (inline in CQ builders)",
        phase_cq=0,
        phase_legacy=2,
    ),
    "build_section_1": AgentStepMeta(
        node_id="build_section_1",
        agent_name="Section 1 Writer (Legacy)",
        layer="Legacy Generation · Section 1",
        action="Five independent LLM writers from bulk KB dump",
        phase_cq=0,
        phase_legacy=3,
    ),
    "build_section_2": AgentStepMeta(
        node_id="build_section_2",
        agent_name="Team + Bios (Legacy)",
        layer="Legacy Generation · Section 2",
        action="Combined team selection + bio fetch",
        phase_cq=0,
        phase_legacy=4,
    ),
    "build_section_3": AgentStepMeta(
        node_id="build_section_3",
        agent_name="Evidence + Cases (Legacy)",
        layer="Legacy Generation · Section 3",
        action="Combined evidence selection + case study writing",
        phase_cq=0,
        phase_legacy=5,
    ),
}


def _banner(title: str, width: int = 78) -> str:
    return "=" * width + f"\n{title}\n" + "=" * width


def _phase_label(meta: AgentStepMeta, *, cq_mode: bool) -> str:
    if cq_mode and meta.phase_cq > 0:
        return f"Phase {meta.phase_cq}/{meta.total_cq}"
    if not cq_mode and meta.phase_legacy > 0:
        return f"Phase {meta.phase_legacy}/{meta.total_legacy}"
    return "Phase —"


def log_pipeline_start(*, rfp_id: str, rfp_client: str, cq_mode: bool) -> None:
    _ensure_file_handler()
    mode = "Company Qualification Layer (Selection → Retrieval → Assembly)" if cq_mode else "Legacy (Retrieval → Writing)"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(
        _banner(
            f"SECTIONS 1–3 PIPELINE START  ({stamp})\n"
            f"  Mode:   {mode}\n"
            f"  RFP:    {rfp_id}\n"
            f"  Client: {rfp_client}\n"
            f"  Log:    {LANGGRAPH_LOG_FILE}"
        )
    )


def log_pipeline_complete(
    *,
    rfp_id: str,
    section_count: int,
    provider: str,
    elapsed_s: float,
    section_ids: list[str] | None = None,
) -> None:
    _ensure_file_handler()
    ids_line = ""
    if section_ids:
        preview = ", ".join(section_ids[:20])
        extra = f" (+{len(section_ids) - 20} more)" if len(section_ids) > 20 else ""
        ids_line = f"\n  IDs:       {preview}{extra}"
    logger.info(
        _banner(
            f"SECTIONS 1–3 PIPELINE COMPLETE\n"
            f"  RFP:       {rfp_id}\n"
            f"  Sections:  {section_count}{ids_line}\n"
            f"  Provider:  {provider}\n"
            f"  Duration:  {elapsed_s:.1f}s\n"
            f"  Log file:  {LANGGRAPH_LOG_FILE}"
        )
    )


def log_agent_start(
    meta: AgentStepMeta,
    *,
    cq_mode: bool,
    rfp_id: str,
    rfp_client: str,
    skipped: bool = False,
) -> float:
    _ensure_file_handler()
    phase = _phase_label(meta, cq_mode=cq_mode)
    status = "SKIPPED (already complete)" if skipped else "RUNNING"
    logger.info(
        _banner(
            f"[{phase}] {meta.agent_name} — {status}\n"
            f"  Node:   {meta.node_id}\n"
            f"  Layer:  {meta.layer}\n"
            f"  Action: {meta.action}\n"
            f"  RFP:    {rfp_id} · {rfp_client}"
        )
    )
    return time.perf_counter()


def log_agent_complete(
    meta: AgentStepMeta,
    *,
    cq_mode: bool,
    elapsed_s: float,
    result: dict[str, Any],
    skipped: bool = False,
) -> None:
    _ensure_file_handler()
    phase = _phase_label(meta, cq_mode=cq_mode)
    if skipped:
        logger.info(f"[{phase}] {meta.agent_name} — SKIPPED ({elapsed_s:.1f}s)")
        return

    summary = _summarize_node_output(meta.node_id, result)
    provider = result.get("provider")
    provider_line = f"\n  Provider: {provider}" if provider else ""
    logger.info(
        _banner(
            f"[{phase}] {meta.agent_name} — DONE ({elapsed_s:.1f}s)\n"
            f"  Output: {summary}{provider_line}"
        )
    )


def log_agent_error(meta: AgentStepMeta, *, cq_mode: bool, elapsed_s: float, error: Exception) -> None:
    _ensure_file_handler()
    phase = _phase_label(meta, cq_mode=cq_mode)
    logger.error(
        _banner(
            f"[{phase}] {meta.agent_name} — FAILED ({elapsed_s:.1f}s)\n"
            f"  Error: {error}"
        )
    )


def _summarize_node_output(node_id: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"ERROR — {result['error']}"

    if node_id == "fetch_knowledge_base":
        return (
            f"voice={len(result.get('kb_zo_voice') or '')} chars · "
            f"company={len(result.get('kb_company') or '')} chars · "
            f"bios={len(result.get('kb_bios') or '')} chars · "
            f"cases={len(result.get('kb_case_studies') or '')} chars"
        )

    if node_id == "fetch_company_truth":
        truth = result.get("company_truth") or {}
        caps = len(truth.get("capabilities") or [])
        sources = len(truth.get("sources") or [])
        legal = truth.get("legalName") or truth.get("legal_name") or "[VERIFY]"
        return f"CompanyTruth JSON · legal={legal!r} · {caps} capabilities · {sources} sources"

    if node_id == "fetch_proposal_context":
        ctx = result.get("proposal_context") or {}
        return (
            f"ProposalContext JSON · industry={ctx.get('industry')!r} · "
            f"type={ctx.get('proposalType') or ctx.get('proposal_type')!r}"
        )

    if node_id == "prioritize_capabilities":
        caps = result.get("prioritized_capabilities") or {}
        p = len(caps.get("primary") or [])
        s = len(caps.get("secondary") or [])
        o = len(caps.get("omit") or [])
        return f"PrioritizedCapabilities · primary={p} · secondary={s} · omit={o}"

    if node_id == "plan_section_1":
        plan = result.get("section1_plan") or {}
        budgets = (plan.get("contentBudget") or plan.get("content_budget") or {}).get("budgets") or []
        section_plan = plan.get("sectionPlan") or plan.get("section_plan") or {}
        return f"Section1Plan · {len(budgets)} budgets · {len(section_plan)} subsection plans"

    if node_id == "build_section_1_cq":
        sections = result.get("sections") or []
        count = sum(1 for s in sections if str(s.get("id", "")).startswith("section-1-"))
        return f"Section 1 Builder · {count} subsection cards"

    if node_id == "select_team":
        team = result.get("team_selection") or {}
        members = team.get("members") or []
        roles = team.get("requiredRoles") or team.get("required_roles") or []
        selected = ", ".join(
            (
                f"{member.get('name')} → {member.get('role') or '[role pending]'}"
                + (
                    f" ({float(member.get('fitScore') or member.get('fit_score') or 0):.2f})"
                    if isinstance(member, dict)
                    else ""
                )
            )
            for member in members
            if isinstance(member, dict) and member.get("name")
        )
        return (
            f"TeamSelection · {len(members)} members · {len(roles)} required roles"
            + (f" · selected: {selected}" if selected else "")
        )

    if node_id == "build_bios":
        sections = result.get("sections") or []
        count = sum(1 for s in sections if str(s.get("id", "")).startswith("section-2-"))
        return f"Bio Builder · {count} bio cards"

    if node_id == "select_evidence":
        ev = result.get("evidence_selection") or {}
        selected = ev.get("selectedStudies") or ev.get("selected_studies") or []
        considered = ev.get("candidatesConsidered") or ev.get("candidates_considered") or 0
        return f"EvidenceSelection · {len(selected)} selected from {considered} candidates"

    if node_id == "build_case_studies":
        sections = result.get("sections") or []
        count = sum(1 for s in sections if str(s.get("id", "")).startswith("section-3-"))
        return f"Case Study Builder · {count} case study cards"

    if node_id == "join_sections":
        return "parallel tracks synchronized"

    if node_id == "validate_sections_editorial":
        review = result.get("section1_editorial_review") or {}
        recs = review.get("recommendations") or []
        pending = sum(1 for r in recs if (r.get("status") or "pending") == "pending")
        return f"EditorialReview · {len(recs)} recommendations · {pending} pending"

    if node_id in {"build_section_1", "build_section_2", "build_section_3"}:
        sections = result.get("sections") or []
        prefix = {
            "build_section_1": "section-1-",
            "build_section_2": "section-2-",
            "build_section_3": "section-3-",
        }[node_id]
        count = sum(1 for s in sections if str(s.get("id", "")).startswith(prefix))
        return f"{count} subsection cards emitted"

    return "complete"


def _is_skipped(meta: AgentStepMeta, state: dict[str, Any]) -> bool:
    s1_nodes = {
        "fetch_company_truth",
        "prioritize_capabilities",
        "plan_section_1",
        "build_section_1_cq",
    }
    if meta.node_id in s1_nodes and state.get("skip_section_1"):
        return True
    if meta.node_id == "fetch_proposal_context" and (
        state.get("skip_section_1") and state.get("skip_section_2") and state.get("skip_section_3")
    ):
        return True
    if meta.node_id in {"select_team", "build_bios"} and state.get("skip_section_2"):
        return True
    if meta.node_id in {"select_evidence", "build_case_studies"} and state.get("skip_section_3"):
        return True
    if meta.node_id == "build_section_1" and state.get("skip_section_1"):
        return True
    if meta.node_id == "build_section_2" and state.get("skip_section_2"):
        return True
    if meta.node_id == "build_section_3" and state.get("skip_section_3"):
        return True
    if meta.node_id == "validate_sections_editorial" and (
        state.get("skip_section_1") and state.get("skip_section_2") and state.get("skip_section_3")
    ):
        return True
    return False


def with_agent_logging(
    node_id: str,
    handler: NodeHandler,
    *,
    cq_mode: bool,
) -> NodeHandler:
    """Wrap a LangGraph node with CrewAI-style start/complete logging."""
    meta = AGENT_STEPS[node_id]

    async def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        skipped = _is_skipped(meta, state)
        started = log_agent_start(
            meta,
            cq_mode=cq_mode,
            rfp_id=str(state.get("rfp_id") or ""),
            rfp_client=str(state.get("rfp_client") or ""),
            skipped=skipped,
        )
        if skipped:
            log_agent_complete(meta, cq_mode=cq_mode, elapsed_s=time.perf_counter() - started, result={}, skipped=True)
            return {}

        try:
            result = await handler(state)
            log_agent_complete(
                meta,
                cq_mode=cq_mode,
                elapsed_s=time.perf_counter() - started,
                result=result if isinstance(result, dict) else {},
            )
            return result
        except Exception as exc:
            log_agent_error(meta, cq_mode=cq_mode, elapsed_s=time.perf_counter() - started, error=exc)
            raise

    wrapped.__name__ = handler.__name__
    return wrapped
