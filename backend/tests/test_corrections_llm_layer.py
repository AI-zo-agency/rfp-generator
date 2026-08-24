"""Standing corrections ride along on every model call."""

import asyncio

from app.services import llm

BLOCK = "## STANDING CORRECTIONS (authoritative)\n- (2026-08-24) Ron Comer has retired"


def _apply(messages, *, node_name=None, include=True):
    return asyncio.run(
        llm.apply_standing_corrections(
            messages, node_name=node_name, include_corrections=include
        )
    )


def _stub_block(monkeypatch, value):
    async def fake_block():
        return value

    import app.services.kb_corrections as kb_corrections

    monkeypatch.setattr(kb_corrections, "corrections_prompt_block", fake_block)


def test_appends_block_to_system_message(monkeypatch) -> None:
    _stub_block(monkeypatch, BLOCK)
    out = _apply(
        [
            {"role": "system", "content": "You write proposals."},
            {"role": "user", "content": "Who is Ron Comer?"},
        ]
    )
    assert "Ron Comer has retired" in out[0]["content"]
    assert out[0]["content"].startswith("You write proposals.")
    assert out[1]["content"] == "Who is Ron Comer?"


def test_inserts_system_message_when_absent(monkeypatch) -> None:
    _stub_block(monkeypatch, BLOCK)
    out = _apply([{"role": "user", "content": "Who is Ron Comer?"}])
    assert out[0]["role"] == "system"
    assert "Ron Comer has retired" in out[0]["content"]
    assert len(out) == 2


def test_does_not_mutate_caller_messages(monkeypatch) -> None:
    _stub_block(monkeypatch, BLOCK)
    messages = [{"role": "system", "content": "You write proposals."}]
    _apply(messages)
    assert messages[0]["content"] == "You write proposals."


def test_no_corrections_leaves_messages_untouched(monkeypatch) -> None:
    _stub_block(monkeypatch, "")
    messages = [{"role": "system", "content": "You write proposals."}]
    assert _apply(messages) == messages


def test_idempotent_when_block_already_present(monkeypatch) -> None:
    _stub_block(monkeypatch, BLOCK)
    messages = [{"role": "system", "content": f"You write proposals.\n\n{BLOCK}"}]
    out = _apply(messages)
    assert out[0]["content"].count("## STANDING CORRECTIONS") == 1


def test_exempt_node_gets_no_corrections(monkeypatch) -> None:
    _stub_block(monkeypatch, BLOCK)
    messages = [{"role": "system", "content": "Plan search queries."}]
    assert _apply(messages, node_name="query_planner") == messages


def test_include_corrections_false_opts_out(monkeypatch) -> None:
    _stub_block(monkeypatch, BLOCK)
    messages = [{"role": "system", "content": "You write proposals."}]
    assert _apply(messages, include=False) == messages


def test_chat_json_applies_corrections(monkeypatch) -> None:
    """The hook is wired into the real entry point, not just callable on its own."""
    seen: dict[str, object] = {}
    _stub_block(monkeypatch, BLOCK)

    async def fake_post(*args, **kwargs):
        seen["messages"] = kwargs.get("messages") or (args[2] if len(args) > 2 else None)
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}, "stub-model"

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    try:
        asyncio.run(
            llm.chat_json(
                [{"role": "system", "content": "You write proposals."}],
                node_name="test_node",
            )
        )
    except Exception:
        pass

    messages = seen.get("messages")
    assert messages is not None, "chat_json did not reach _post_chat"
    assert any("Ron Comer has retired" in (m.get("content") or "") for m in messages)
