"""Financial spend must land in its own ledger and nowhere near the proposal one.

The two domains share no RFP, no run, and no reporting surface. A row in the
wrong table is not a cosmetic problem: get_global_cost_summary sweeps every row
in llm_call_log and _attach_titles decorates them with RFP titles.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.financial import financial_llm_cost
from app.services import llm


class _FakeTable:
    def __init__(self, sink):
        self.sink = sink
        self.sink["filters"] = []

    def insert(self, row):
        self.sink["inserted"] = row
        return self

    def select(self, cols):
        self.sink["selected"] = cols
        return self

    def eq(self, col, val):
        self.sink["filters"].append((col, val))
        return self

    def order(self, col):
        return self

    def execute(self):
        return MagicMock(data=self.sink.get("rows", []))


@pytest.fixture
def ledger(monkeypatch):
    sink: dict = {}
    client = MagicMock()

    def _table(name):
        sink["table"] = name
        return _FakeTable(sink)

    client.table.side_effect = _table
    monkeypatch.setattr(financial_llm_cost, "_get_client", lambda: client)
    sink["client"] = client
    return sink


def test_a_call_is_written_to_the_financial_ledger(ledger):
    financial_llm_cost.record_call(
        thread_id="t1", turn_id="u1", node_name="qb_chat.answer",
        model="gemini-2.5-flash", tier="light", provider="gemini",
        usage={"prompt_tokens": 1000, "completion_tokens": 200},
        latency_ms=400,
    )

    ledger["client"].table.assert_called_with("financial_llm_calls")
    row = ledger["inserted"]
    assert row["thread_id"] == "t1"
    assert row["turn_id"] == "u1"
    assert row["input_tokens"] == 1000
    # No column exists to hold one, which is the point.
    assert "rfp_id" not in row
    assert "run_id" not in row


def test_a_write_failure_never_breaks_the_answer(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(financial_llm_cost, "_get_client", boom)
    # Returns rather than raises — the reader already has their answer.
    financial_llm_cost.record_call(
        thread_id="t1", turn_id="u1", node_name="n", model="m", tier="light",
        provider="gemini", usage={}, latency_ms=1,
    )


def test_a_read_failure_reports_zero_so_the_cap_fails_open(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(financial_llm_cost, "_get_client", boom)
    assert financial_llm_cost.thread_total_usd("t1") == 0.0


def test_thread_total_sums_only_this_thread(ledger):
    ledger["rows"] = [{"cost_usd": 0.01}, {"cost_usd": 0.02}]
    total = financial_llm_cost.thread_total_usd("t1")

    assert total == pytest.approx(0.03)
    assert ("thread_id", "t1") in ledger["filters"]


# ── the llm.py seam ────────────────────────────────────────────────────────

def _call_chat_text(monkeypatch, **kwargs):
    monkeypatch.setattr(llm.settings, "gemini_api_key", "AIzaSyRealLookingKey123456")
    with patch.object(
        llm, "_post_gemini_chat",
        AsyncMock(return_value=("hello", {"prompt_tokens": 10, "completion_tokens": 2})),
    ):
        return asyncio.run(llm.chat_text([{"role": "user", "content": "hi"}], **kwargs))


def test_a_cost_sink_diverts_accounting_away_from_llm_call_log(monkeypatch):
    seen = []
    with patch.object(llm, "_record_successful_call") as proposal_ledger:
        raw, provider = _call_chat_text(
            monkeypatch,
            cost_sink=lambda **kw: seen.append(kw),
        )

    assert (raw, provider) == ("hello", "gemini")
    assert proposal_ledger.call_count == 0, "financial spend reached the proposal ledger"
    assert len(seen) == 1
    assert seen[0]["provider"] == "gemini"
    assert seen[0]["usage"]["prompt_tokens"] == 10


def test_a_cost_sink_also_opts_out_of_the_proposal_run_budget(monkeypatch):
    """The proposal cap reads a pipeline phase off a contextvar. A financial
    call made while a proposal is in flight must not inherit that budget."""
    with patch.object(llm, "_enforce_run_cost_cap") as cap, \
         patch.object(llm, "_record_successful_call"):
        _call_chat_text(monkeypatch, cost_sink=lambda **kw: None)

    assert cap.call_count == 0


def test_without_a_cost_sink_nothing_changes_for_existing_callers(monkeypatch):
    with patch.object(llm, "_record_successful_call") as proposal_ledger, \
         patch.object(llm, "_enforce_run_cost_cap") as cap:
        _call_chat_text(monkeypatch)

    assert proposal_ledger.call_count == 1
    assert cap.call_count == 1
    assert proposal_ledger.call_args.kwargs["provider"] == "gemini"
