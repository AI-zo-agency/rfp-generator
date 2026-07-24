"""Contextvars for LLM call instrumentation (rfp_id / run_id / node_name).

Graph wrappers and generate_full_proposal set these so chat_json/chat_text
can log without requiring every agent call site to pass IDs.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_llm_node_name: ContextVar[str] = ContextVar("llm_node_name", default="")
_llm_rfp_id: ContextVar[str] = ContextVar("llm_rfp_id", default="")
_llm_run_id: ContextVar[str] = ContextVar("llm_run_id", default="")


def get_llm_node_name() -> str:
    return _llm_node_name.get() or ""


def get_llm_rfp_id() -> str:
    return _llm_rfp_id.get() or ""


def get_llm_run_id() -> str:
    return _llm_run_id.get() or ""


def set_llm_node_name(name: str) -> Token[str]:
    return _llm_node_name.set(name or "")


def reset_llm_node_name(token: Token[str]) -> None:
    _llm_node_name.reset(token)


def set_llm_rfp_id(rfp_id: str) -> Token[str]:
    return _llm_rfp_id.set(rfp_id or "")


def reset_llm_rfp_id(token: Token[str]) -> None:
    _llm_rfp_id.reset(token)


def set_llm_run_id(run_id: str) -> Token[str]:
    return _llm_run_id.set(run_id or "")


def reset_llm_run_id(token: Token[str]) -> None:
    _llm_run_id.reset(token)


@contextmanager
def llm_call_context(
    *,
    rfp_id: str | None = None,
    run_id: str | None = None,
    node_name: str | None = None,
) -> Iterator[None]:
    """Temporarily set instrumentation context for nested LLM calls."""
    tokens: list[tuple[str, Token[str]]] = []
    if rfp_id is not None:
        tokens.append(("rfp", set_llm_rfp_id(rfp_id)))
    if run_id is not None:
        tokens.append(("run", set_llm_run_id(run_id)))
    if node_name is not None:
        tokens.append(("node", set_llm_node_name(node_name)))
    try:
        yield
    finally:
        for kind, token in reversed(tokens):
            if kind == "rfp":
                reset_llm_rfp_id(token)
            elif kind == "run":
                reset_llm_run_id(token)
            else:
                reset_llm_node_name(token)
