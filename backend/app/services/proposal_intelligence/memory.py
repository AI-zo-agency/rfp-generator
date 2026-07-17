"""Proposal Memory upsert helpers."""

from __future__ import annotations

from app.services.proposal_intelligence.schemas import ProposalMemory


def upsert_memory(
    memory: ProposalMemory,
    agent: str,
    facts: dict[str, str],
) -> ProposalMemory:
    """Merge non-empty facts into proposal memory and record the updating agent."""
    merged = dict(memory.facts)
    for key, value in facts.items():
        text = str(value or "").strip()
        if not text:
            continue
        merged[str(key)] = text
    updated_by = list(memory.updated_by)
    if agent and agent not in updated_by:
        updated_by.append(agent)
    return ProposalMemory(
        facts=merged,
        updatedBy=updated_by,
        confidence=memory.confidence,
    )
