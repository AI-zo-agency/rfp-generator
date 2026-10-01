import importlib.util
from pathlib import Path

import pytest

from app.services import brand_voice_revisions as bvr
from tests.fake_supabase import FakeDb

pytestmark = pytest.mark.real_revisions

ROOT = Path(__file__).resolve().parents[2]
REV6 = (ROOT / "branding" / "ZO_BRAND_AND_WRITING_STANDARDS_REV6.md").read_text(encoding="utf-8")
REV7 = (ROOT / "branding" / "ZO_BRAND_AND_WRITING_STANDARDS_REV7.md").read_text(encoding="utf-8")


def _load_seed():
    path = ROOT / "backend" / "scripts" / "seed_brand_voice_revisions.py"
    spec = importlib.util.spec_from_file_location("seed_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(monkeypatch):
    bvr._reset_caches()
    fake = FakeDb()
    monkeypatch.setattr(bvr, "enabled", lambda: True)
    monkeypatch.setattr(bvr, "_db", lambda: fake)
    yield fake
    bvr._reset_caches()


def _by_label(db):
    return {r["label"]: r for r in db.tables["brand_voice_revisions"]}


def test_seed_stores_rev6_and_rev7_and_defaults_to_rev6(db, capsys):
    _load_seed().main()
    revs = _by_label(db)
    assert set(revs) == {"rev 6", "rev 7"}
    assert revs["rev 6"]["body"] == REV6
    assert revs["rev 7"]["body"] == REV7
    assert db.tables["brand_voice_active"][0]["revision_id"] == revs["rev 6"]["id"]
    assert "default set to rev 6" in capsys.readouterr().out


def test_second_run_adds_and_changes_nothing(db, capsys):
    seed = _load_seed()
    seed.main()
    capsys.readouterr()
    before = {k: [dict(r) for r in v] for k, v in db.tables.items()}
    seed.main()
    out = capsys.readouterr().out
    assert db.tables == before
    assert "rev 6 already stored" in out and "rev 7 already stored" in out
    assert "default already set; left alone" in out


def test_existing_default_is_not_overwritten(db, capsys):
    rev7 = bvr.add_revision(label="rev 7", body=REV7, created_by="someone")
    bvr.set_active(rev7.id, updated_by="someone")
    _load_seed().main()
    out = capsys.readouterr().out
    assert db.tables["brand_voice_active"][0]["revision_id"] == rev7.id
    assert len(db.tables["brand_voice_revisions"]) == 2
    assert "default already set; left alone" in out and "default set to rev 6" not in out


def test_exits_when_supabase_is_not_configured(monkeypatch):
    monkeypatch.setattr(bvr, "enabled", lambda: False)
    with pytest.raises(SystemExit) as exc:
        _load_seed().main()
    assert "Supabase is not configured" in str(exc.value)
