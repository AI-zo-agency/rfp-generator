"""Every gate agent must resolve to a real profile and a registered routing key.

An AgentProfile whose node_name is not in llm_routing's stage map falls through to the
cheapest provider silently — nothing errors, nothing logs, the model is just worse. That
is the wrong failure mode for a claim verifier deciding whether a fact is true.
"""

from __future__ import annotations

import pytest

from app.services.llm_routing import _QUALITY_EXACT
from app.services.proposal_langchain_agents import (
    AGENT_PROFILES,
    AgentRole,
    get_profile,
)

GATE_ROLES = [
    AgentRole.CLAIM_VERIFIER,
    AgentRole.EVALUATOR,
    AgentRole.CONSISTENCY_AUDITOR,
    AgentRole.REPETITION_AUDITOR,
    AgentRole.SLOP_AUDITOR,
    AgentRole.MANUAL_FILL_TRIAGE,
]


@pytest.mark.parametrize("role", GATE_ROLES, ids=lambda r: r.value)
def test_role_has_a_profile(role):
    assert get_profile(role) is not None


@pytest.mark.parametrize("role", GATE_ROLES, ids=lambda r: r.value)
def test_role_declares_a_node_name(role):
    assert get_profile(role).node_name, f"{role.value} would fall through to cheapest"


@pytest.mark.parametrize("role", GATE_ROLES, ids=lambda r: r.value)
def test_node_name_is_registered_for_quality_routing(role):
    node = get_profile(role).node_name
    assert node in _QUALITY_EXACT, f"{node} is not routed to a quality model"


@pytest.mark.parametrize("role", GATE_ROLES, ids=lambda r: r.value)
def test_role_has_a_system_prompt(role):
    assert len(get_profile(role).system_prompt.strip()) > 100


def test_cutting_agents_have_no_kb_tools():
    """Slop and repetition only delete and restyle.

    Granting retrieval to a stylist invites it to add content while "improving tone" —
    the exact failure being guarded against.
    """
    assert get_profile(AgentRole.REPETITION_AUDITOR).max_tool_rounds == 0
    assert get_profile(AgentRole.SLOP_AUDITOR).max_tool_rounds == 0


def test_every_profile_key_matches_its_role():
    for role, profile in AGENT_PROFILES.items():
        assert profile.role == role


def test_no_two_roles_share_a_node_name():
    nodes = [p.node_name for p in AGENT_PROFILES.values() if p.node_name]
    assert len(nodes) == len(set(nodes))
