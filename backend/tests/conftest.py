"""Route tests exercise handlers, not sign-in. test_auth_guard.py checks sign-in itself."""

import sys

import pytest


@pytest.fixture(autouse=True)
def _signed_in(request):
    # Only touch the app if this test module already imported it: importing
    # app.main has side effects (LangSmith env setup) that other tests must not inherit.
    main = sys.modules.get("app.main")
    if main is None or request.node.get_closest_marker("real_auth"):
        yield
        return
    from app.api.auth_guard import require_user

    main.app.dependency_overrides[require_user] = lambda: None
    yield
    main.app.dependency_overrides.pop(require_user, None)


@pytest.fixture(autouse=True)
def _no_live_voice_llm(request, monkeypatch):
    """The voice pass calls a real model. Keep it out of every test that does not ask for it."""
    if request.node.get_closest_marker("real_voice_llm"):
        return
    from app.services import proposal_voice_llm

    async def _noop(text, **_kw):
        return proposal_voice_llm.VoiceResult(text=text or "")

    monkeypatch.setattr(proposal_voice_llm, "rewrite_for_voice", _noop)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: run with the real require_user dependency")
    config.addinivalue_line("markers", "real_voice_llm: exercise proposal_voice_llm itself (the model is stubbed by the test)")
