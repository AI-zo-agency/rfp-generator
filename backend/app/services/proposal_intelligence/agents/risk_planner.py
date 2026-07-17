"""Risk / QA / Communication / Training planners (playbook-informed)."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.retrieval import IntelligenceBucket, retrieve_intelligence
from app.services.proposal_intelligence.schemas import (
    CommunicationPlan,
    ProposalExecutionPlan,
    QaPlan,
    RiskPlan,
    TrainingPlan,
)

logger = logging.getLogger(__name__)


async def _run_simple_planner(
    *,
    plan: ProposalExecutionPlan,
    agent: str,
    system: str,
    bucket: IntelligenceBucket,
    query: str,
    model_cls: type,
    assign: Callable[[ProposalExecutionPlan, Any], None],
    decision_text_fn: Callable[[Any], str],
) -> ProposalExecutionPlan:
    hits = await retrieve_intelligence(bucket, query=query, limit=4)
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Understanding:\n{plan.opportunity.understanding.model_dump_json()}\n"
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"Delivery model:\n{plan.delivery.delivery_model.model_dump_json()}\n"
                    f"Playbook/standards intel:\n{json.dumps(hits, indent=2)[:10000]}"
                ),
            },
        ],
        max_tokens=1536,
        agent_name=agent,
    )
    try:
        artifact = model_cls.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", agent, exc)
        artifact = model_cls(confidence=0.2)
    if hasattr(artifact, "confidence"):
        artifact.confidence = clamp_confidence(artifact.confidence)
    assign(plan, artifact)
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=agent,
        decision_text=decision_text_fn(artifact),
        reason=f"Planned via {bucket} intelligence",
        confidence=float(getattr(artifact, "confidence", 0.0) or 0.0),
    )
    return plan


async def run_risk_planner(*, plan: ProposalExecutionPlan, rfp_meta: dict[str, str] | None = None) -> ProposalExecutionPlan:
    return await _run_simple_planner(
        plan=plan,
        agent="risk_planner",
        system=(
            "Risk Planner. Return JSON only: "
            '{"risks":[{"risk":"","likelihood":"","impact":"","mitigation":""}],"confidence":0.0}'
        ),
        bucket="playbooks",
        query="project risk playbook mitigation",
        model_cls=RiskPlan,
        assign=lambda p, a: setattr(p.delivery, "risk", a),
        decision_text_fn=lambda a: f"Risks identified: {len(a.risks)}",
    )


async def run_qa_planner(*, plan: ProposalExecutionPlan, rfp_meta: dict[str, str] | None = None) -> ProposalExecutionPlan:
    return await _run_simple_planner(
        plan=plan,
        agent="qa_planner",
        system=(
            "QA Planner. Return JSON only: "
            '{"approach":"string","gates":["string"],"confidence":0.0}'
        ),
        bucket="standards",
        query="QA standards accessibility quality gates",
        model_cls=QaPlan,
        assign=lambda p, a: setattr(p.delivery, "qa", a),
        decision_text_fn=lambda a: f"QA gates: {len(a.gates)}",
    )


async def run_communication_planner(
    *, plan: ProposalExecutionPlan, rfp_meta: dict[str, str] | None = None
) -> ProposalExecutionPlan:
    return await _run_simple_planner(
        plan=plan,
        agent="communication_planner",
        system=(
            "Communication Planner. Return JSON only: "
            '{"cadence":"string","channels":["string"],"reportingPlan":"string","confidence":0.0}'
        ),
        bucket="playbooks",
        query="project communication playbook reporting cadence",
        model_cls=CommunicationPlan,
        assign=lambda p, a: setattr(p.delivery, "communication", a),
        decision_text_fn=lambda a: f"Comm cadence: {a.cadence or 'unset'}",
    )


async def run_training_planner(
    *, plan: ProposalExecutionPlan, rfp_meta: dict[str, str] | None = None
) -> ProposalExecutionPlan:
    return await _run_simple_planner(
        plan=plan,
        agent="training_planner",
        system=(
            "Training & Transition Planner. Return JSON only: "
            '{"trainingPlan":"string","transitionPlan":"string","confidence":0.0}'
        ),
        bucket="playbooks",
        query="training transition playbook knowledge transfer",
        model_cls=TrainingPlan,
        assign=lambda p, a: setattr(p.delivery, "training", a),
        decision_text_fn=lambda a: "Training/transition planned",
    )
