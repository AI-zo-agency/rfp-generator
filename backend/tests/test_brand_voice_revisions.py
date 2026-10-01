import pytest

from app.services import brand_voice_revisions as bvr
from tests.fake_supabase import FakeDb

pytestmark = pytest.mark.real_revisions

MD = "# T\n\n## 1. Company name\nAlways zö.\n\n## 2. Writing rules\nWrite plainly.\n"

FULL = """# zö Brand & Writing Standards

**rev 7 · September 2026 · confidential**

---

## What changed in rev 7

Change note.

---

## 1. Company name

Always zö agency.

---

## 5. Typography

Anton 22pt.

---

## 8. Checklist. Run this every time.

**Words**

- Em dashes removed?

**Look**

- White ground?

---

## 9. Image direction

Dramatic light.

---

## 10. Exceptions register

Tagline.
"""


@pytest.fixture(autouse=True)
def db(monkeypatch):
    bvr._reset_caches()
    fake = FakeDb()
    monkeypatch.setattr(bvr, "enabled", lambda: True)
    monkeypatch.setattr(bvr, "_db", lambda: fake)
    yield fake
    bvr._reset_caches()


def test_words_only_keeps_rules_and_drops_look_and_notes():
    out = bvr.words_only(FULL)
    assert "## 1. Company name" in out and "Em dashes removed?" in out and "Tagline." in out
    assert "**rev 7" in out
    for gone in ("Change note", "Anton 22pt", "White ground", "Dramatic light", "**Look**"):
        assert gone not in out


def test_words_only_on_the_repo_rev6_file():
    out = bvr.words_only(bvr.builtin().body)
    heads = [line for line in out.splitlines() if line.startswith("## ")]
    assert heads[0] == "## 1. Company name" and heads[-1].startswith("## 10.")
    assert "strongest advocate" in out


def test_without_supabase_only_the_repo_file_exists(monkeypatch):
    monkeypatch.setattr(bvr, "enabled", lambda: False)
    assert [r.id for r in bvr.list_revisions()] == [bvr.BUILTIN_ID]
    assert bvr.active_revision().id == bvr.BUILTIN_ID


def test_add_list_get_and_dedupe():
    a = bvr.add_revision(label="rev 6", body=MD, created_by="s@zo.agency")
    b = bvr.add_revision(label="rev 7", body=MD + "\nMore.\n", created_by="s@zo.agency", notes=" n ")
    assert [r.label for r in bvr.list_revisions()] == ["rev 7", "rev 6"]
    assert bvr.get_revision(a.id).body == MD and b.notes == "n"
    with pytest.raises(bvr.DuplicateRevision) as exc:
        bvr.add_revision(label="again", body=MD, created_by="x")
    assert exc.value.existing_id == a.id


@pytest.mark.parametrize(
    "label,body",
    [("", MD), ("x" * 41, MD), ("rev", "  "), ("rev", "just text"), ("rev", "x" * 200_001)],
)
def test_validation_rejects(label, body):
    with pytest.raises(bvr.RevisionError):
        bvr.add_revision(label=label, body=body, created_by="x")


def test_active_defaults_to_builtin_then_follows_set_active():
    assert bvr.active_revision().id == bvr.BUILTIN_ID
    a = bvr.add_revision(label="rev 6", body=MD, created_by="x")
    assert bvr.active_pointer_id() is None
    bvr.set_active(a.id, updated_by="x")
    assert bvr.active_revision().id == a.id and bvr.active_pointer_id() == a.id


def test_set_active_rejects_unknown_and_builtin():
    with pytest.raises(bvr.RevisionError):
        bvr.set_active("nope", updated_by="x")
    with pytest.raises(bvr.RevisionError):
        bvr.set_active(bvr.BUILTIN_ID, updated_by="x")


def test_active_pointer_read_failure_falls_back(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(bvr, "active_pointer_id", boom)
    assert bvr.active_revision().id == bvr.BUILTIN_ID
