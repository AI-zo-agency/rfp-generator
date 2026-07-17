"""Mutate ProposalExecutionPlan safely."""

from __future__ import annotations

from app.services.proposal_intelligence.agent_base import clamp_confidence, decision
from app.services.proposal_intelligence.memory import upsert_memory
from app.services.proposal_intelligence.schemas import DecisionLogEntry, ProposalExecutionPlan


class IntelligenceError(Exception):
    """Hard failure for critical Path agents (RFP Understanding)."""


def append_decision(
    plan: ProposalExecutionPlan,
    *,
    agent: str,
    decision_text: str,
    reason: str,
    confidence: float,
) -> ProposalExecutionPlan:
    entry = decision(agent, decision_text, reason, confidence)
    plan.decision_log.append(entry)
    return plan


def merge_memory(
    plan: ProposalExecutionPlan,
    agent: str,
    facts: dict[str, str],
) -> ProposalExecutionPlan:
    plan.proposal_memory = upsert_memory(plan.proposal_memory, agent, facts)
    return plan


def set_provider(plan: ProposalExecutionPlan, provider: str) -> ProposalExecutionPlan:
    if provider and provider != "none":
        plan.metadata.provider = provider
    return plan
