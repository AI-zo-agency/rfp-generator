import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.proposal import ProposalDraft
from app.services import brand_voice_revisions as bvr

pytestmark = pytest.mark.real_revisions

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_voice_rev_pin.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _draft(rfp_id, pin=None):
    return ProposalDraft(rfpId=rfp_id, sections=[], updatedAt="2026-09-30T00:00:00Z", voiceRevId=pin)


@pytest.fixture
def script(monkeypatch):
    mod = _load()
    drafts = {"a": _draft("a"), "b": _draft("b", "kept")}
    saved = []
    monkeypatch.setattr(mod, "list_rfps", lambda: [SimpleNamespace(id=k) for k in drafts])
    monkeypatch.setattr(mod, "get_proposal_draft", lambda rfp_id: drafts[rfp_id])
    monkeypatch.setattr(mod, "save_proposal_draft", saved.append)
    monkeypatch.setattr(bvr, "enabled", lambda: True)
    monkeypatch.setattr(bvr, "active_revision", lambda: SimpleNamespace(id="rev-id", label="rev 6"))
    mod.saved = saved
    return mod


def test_pins_only_unpinned_drafts(script, capsys):
    script.main(dry_run=False)
    assert [d.rfp_id for d in script.saved] == ["a"] and script.saved[0].voice_rev_id == "rev-id"
    assert "pinned 1, left alone 1" in capsys.readouterr().out


def test_dry_run_saves_nothing(script):
    script.main(dry_run=True)
    assert script.saved == []


def test_refuses_when_the_default_is_the_builtin_stub(script, monkeypatch):
    monkeypatch.setattr(bvr, "active_revision", lambda: SimpleNamespace(id=bvr.BUILTIN_ID, label="rev 6"))
    with pytest.raises(SystemExit) as exc:
        script.main(dry_run=False)
    assert "seed" in str(exc.value).lower() and script.saved == []
