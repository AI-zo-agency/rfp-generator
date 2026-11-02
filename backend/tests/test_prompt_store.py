"""Prompts page storage and the live lookup agents use. Never touches real Supabase."""

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import prompt_catalog, prompt_store
from app.services.prompt_catalog import CATALOG, PromptSpec


class _Query:
    def __init__(self, db):
        self.db, self.filter, self.limit_n, self.payload, self.order_key = db, None, None, None, None

    def select(self, columns):
        self.db.selects.append(columns)
        return self

    def eq(self, key, value):
        self.filter = (key, value)
        return self

    def order(self, key, desc=False):
        self.order_key = key
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def update(self, payload):
        self.payload = ("update", payload)
        return self

    def upsert(self, payload, on_conflict=None):
        self.payload = ("upsert", payload)
        return self

    def execute(self):
        if self.db.fail:
            raise self.db.fail
        if self.payload and self.payload[0] == "update":
            self.db.rows[self.filter[1]].update(self.payload[1])
            return SimpleNamespace(data=[])
        if self.payload:
            for row in self.payload[1]:
                self.db.rows[row["key"]] = {**self.db.rows.get(row["key"], {}), **row}
            return SimpleNamespace(data=[])
        rows = [
            {**r, "is_edited": r["body"] != r["default_body"]}
            for r in self.db.rows.values()
            if not self.filter or r[self.filter[0]] == self.filter[1]
        ]
        if self.order_key:
            rows.sort(key=lambda r: r[self.order_key], reverse=True)
        return SimpleNamespace(data=rows[: self.limit_n] if self.limit_n else rows)


class _Db:
    def __init__(self):
        self.rows, self.selects, self.fail = {}, [], None

    def table(self, _name):
        return _Query(self)


def _row(key="a.b", body="live", default="default", updated_at="2026-11-01T00:00:00+00:00"):
    return {
        "key": key,
        "group_name": "G",
        "name": "N",
        "description": "",
        "source": "",
        "body": body,
        "default_body": default,
        "updated_at": updated_at,
        "updated_by": None,
    }


@pytest.fixture()
def db(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(prompt_store, "enabled", lambda: True)
    monkeypatch.setattr(prompt_store, "_client", lambda: fake)
    prompt_store.reset_cache()
    yield fake
    prompt_store.reset_cache()


def test_every_catalog_default_exists_and_keys_are_unique():
    assert len({s.key for s in CATALOG}) == len(CATALOG)
    for spec in CATALOG:
        default = getattr(importlib.import_module(spec.module), spec.attr)
        assert isinstance(default, str) and default.strip(), spec.key


def test_get_prompt_prefers_supabase_and_falls_back_to_code(db):
    db.rows["a.b"] = _row()
    assert prompt_store.get_prompt("a.b", "code") == "live"
    assert prompt_store.get_prompt("not.seeded", "code") == "code"


def test_get_prompt_uses_code_default_when_supabase_is_down(db):
    db.fail = RuntimeError("relation agent_prompts does not exist")
    assert prompt_store.get_prompt("a.b", "code") == "code"


def test_get_prompt_keeps_last_good_body_when_supabase_goes_down(db, monkeypatch):
    db.rows["a.b"] = _row()
    assert prompt_store.get_prompt("a.b", "code") == "live"
    db.fail = RuntimeError("timeout")
    monkeypatch.setattr(prompt_store, "_next_check", 0.0)
    assert prompt_store.get_prompt("a.b", "code") == "live"


def test_checks_read_bodies_only_after_something_changed(db, monkeypatch):
    db.rows["a.b"] = _row()
    prompt_store.get_prompt("a.b", "code")
    db.selects.clear()
    monkeypatch.setattr(prompt_store, "_next_check", 0.0)
    prompt_store.get_prompt("a.b", "code")
    assert db.selects == ["updated_at"]

    db.rows["a.b"].update(body="newer", updated_at="2026-11-02T00:00:00+00:00")
    monkeypatch.setattr(prompt_store, "_next_check", 0.0)
    assert prompt_store.get_prompt("a.b", "code") == "newer"


def test_save_rejects_a_stale_editor(db):
    db.rows["a.b"] = _row()
    with pytest.raises(prompt_store.PromptConflict):
        prompt_store.save_prompt("a.b", "x", editor="e", expected_updated_at="2020-01-01T00:00:00+00:00")
    assert db.rows["a.b"]["body"] == "live"


def test_save_then_reset_reach_this_process_immediately(db):
    db.rows["a.b"] = _row()
    assert prompt_store.get_prompt("a.b", "code") == "live"
    prompt_store.save_prompt("a.b", "edited", editor="e@zo.agency")
    assert prompt_store.get_prompt("a.b", "code") == "edited"
    assert db.rows["a.b"]["updated_by"] == "e@zo.agency"
    prompt_store.reset_prompt("a.b", editor="e@zo.agency")
    assert prompt_store.get_prompt("a.b", "code") == "default"


def _sync_with(monkeypatch, default):
    module = SimpleNamespace(ATTR=default)
    monkeypatch.setattr(prompt_store.importlib, "import_module", lambda _name: module)
    monkeypatch.setattr(prompt_catalog, "CATALOG", (PromptSpec("a.b", "G", "N", "mod", "ATTR", "desc"),))
    return prompt_store.sync_catalog()


def test_sync_inserts_new_prompts_with_the_code_text(db, monkeypatch):
    _sync_with(monkeypatch, "from code")
    assert db.rows["a.b"]["body"] == "from code"
    assert db.rows["a.b"]["default_body"] == "from code"


def test_sync_lets_unedited_prompts_follow_code(db, monkeypatch):
    _sync_with(monkeypatch, "v1")
    _sync_with(monkeypatch, "v2")
    assert db.rows["a.b"]["body"] == "v2"


def test_sync_never_overwrites_an_edit_but_moves_its_default(db, monkeypatch):
    _sync_with(monkeypatch, "v1")
    db.rows["a.b"]["body"] = "someone's edit"
    _sync_with(monkeypatch, "v2")
    assert db.rows["a.b"]["body"] == "someone's edit"
    assert db.rows["a.b"]["default_body"] == "v2"


def test_sync_sends_one_column_set_with_body_on_every_row(db, monkeypatch):
    _sync_with(monkeypatch, "v1")
    db.rows["a.b"]["body"] = "someone's edit"
    batches = []
    original = _Query.upsert

    def spy(self, payload, on_conflict=None):
        batches.append(payload)
        return original(self, payload, on_conflict)

    monkeypatch.setattr(_Query, "upsert", spy)
    _sync_with(monkeypatch, "v2")
    assert batches and all({"body", "updated_at", "default_body"} <= set(row) for row in batches[0])


def test_sync_writes_nothing_when_nothing_changed(db, monkeypatch):
    _sync_with(monkeypatch, "v1")
    assert _sync_with(monkeypatch, "v1")["written"] == 0


@pytest.fixture()
def client(db, monkeypatch, sqlite_activity_store):
    monkeypatch.setattr(prompt_catalog, "CATALOG", (PromptSpec("a.b", "G", "N", "mod", "ATTR"),))
    db.rows["a.b"] = _row()
    db.rows["retired.prompt"] = _row(key="retired.prompt")
    return TestClient(app)


def test_api_lists_catalog_prompts_without_bodies(client):
    prompts = client.get("/api/v1/prompts").json()["prompts"]
    assert [p["key"] for p in prompts] == ["a.b"]
    assert prompts[0]["isEdited"] is True
    assert "body" not in prompts[0]


def test_api_save_and_reset(client):
    detail = client.get("/api/v1/prompts/a.b").json()
    assert detail["body"] == "live"
    saved = client.put(
        "/api/v1/prompts/a.b", json={"body": "new", "expectedUpdatedAt": detail["updatedAt"]}
    )
    assert saved.status_code == 200 and saved.json()["body"] == "new"
    assert client.post("/api/v1/prompts/a.b/reset").json()["body"] == "default"


def test_api_refuses_empty_and_stale_saves(client):
    assert client.put("/api/v1/prompts/a.b", json={"body": "  "}).status_code == 422
    stale = client.put("/api/v1/prompts/a.b", json={"body": "x", "expectedUpdatedAt": "2020"})
    assert stale.status_code == 409


def test_api_missing_prompt_is_404(client):
    assert client.get("/api/v1/prompts/nope").status_code == 404


def test_api_missing_table_explains_the_migration(client, db):
    db.fail = RuntimeError("relation agent_prompts does not exist")
    res = client.get("/api/v1/prompts")
    assert res.status_code == 503
    assert "migration" in res.json()["detail"]


def _stub_llm(monkeypatch, reply):
    from app.services import prompt_assist

    async def fake(messages, **kwargs):
        fake.messages = messages
        return reply, "stub"

    monkeypatch.setattr(prompt_assist.llm, "chat_json", fake)
    prompt_assist._guides.clear()
    return fake


def test_assist_returns_a_suggestion_without_saving(client, db, monkeypatch):
    db.rows["a.b"]["body"] = 'Be formal. Return JSON: {"content": "..."}'
    _stub_llm(monkeypatch, {"ok": True, "revised": 'Be friendly. Return JSON: {"content": "..."}', "summary": ["Made the tone friendly"]})
    res = client.post(
        "/api/v1/prompts/a.b/assist",
        json={"instruction": "make it friendlier", "currentBody": db.rows["a.b"]["body"]},
    )
    assert res.status_code == 200
    assert res.json()["summary"] == ["Made the tone friendly"]
    assert db.rows["a.b"]["body"].startswith("Be formal")


def test_assist_refuses_a_change_that_drops_the_reply_format(client, db, monkeypatch):
    _stub_llm(monkeypatch, {"ok": True, "revised": "Be friendly.", "summary": []})
    res = client.post(
        "/api/v1/prompts/a.b/assist",
        json={"instruction": "friendlier", "currentBody": 'Be formal. Return JSON: {"content": "x"}'},
    )
    assert res.status_code == 422 and "reply format" in res.json()["detail"]


def test_assist_passes_the_models_refusal_on_in_plain_words(client, db, monkeypatch):
    _stub_llm(monkeypatch, {"ok": False, "note": "That would let the helper invent facts."})
    res = client.post("/api/v1/prompts/a.b/assist", json={"instruction": "let it make things up", "currentBody": "x"})
    assert res.status_code == 422 and res.json()["detail"] == "That would let the helper invent facts."


def test_assist_rejects_blank_and_unchanged_requests(client, db, monkeypatch):
    _stub_llm(monkeypatch, {"ok": True, "revised": "same", "summary": []})
    assert client.post("/api/v1/prompts/a.b/assist", json={"instruction": "  ", "currentBody": "same"}).status_code == 422
    assert client.post("/api/v1/prompts/a.b/assist", json={"instruction": "do it", "currentBody": "same"}).status_code == 422


def test_guide_is_cached_and_degrades_to_empty(client, db, monkeypatch):
    fake = _stub_llm(monkeypatch, {"what": "It checks facts.", "when": "If it misses errors."})
    assert client.get("/api/v1/prompts/a.b/guide").json() == {"what": "It checks facts.", "when": "If it misses errors."}
    fake.messages = None
    client.get("/api/v1/prompts/a.b/guide")
    assert fake.messages is None  # second call served from cache

    from app.services import prompt_assist

    async def boom(messages, **kwargs):
        raise RuntimeError("no key")

    prompt_assist._guides.clear()
    monkeypatch.setattr(prompt_assist.llm, "chat_json", boom)
    assert client.get("/api/v1/prompts/a.b/guide").json() == {"what": "", "when": ""}


def test_guide_is_saved_and_rewritten_only_when_the_prompt_text_changes(client, db, monkeypatch):
    from app.services import prompt_assist, prompt_store

    saved: dict[str, tuple[str, dict]] = {}
    monkeypatch.setattr(prompt_store, "load_guide", lambda k, h: saved[k][1] if k in saved and saved[k][0] == h else None)
    monkeypatch.setattr(prompt_store, "store_guide", lambda k, h, what, when: saved.__setitem__(k, (h, {"what": what, "when": when})))
    calls = []

    async def fake(messages, **kwargs):
        calls.append(1)
        return {"what": f"v{len(calls)}", "when": "w"}, "stub"

    monkeypatch.setattr(prompt_assist.llm, "chat_json", fake)
    prompt_assist._guides.clear()

    assert client.get("/api/v1/prompts/a.b/guide").json()["what"] == "v1"
    prompt_assist._guides.clear()  # as after a backend restart
    assert client.get("/api/v1/prompts/a.b/guide").json()["what"] == "v1"
    assert len(calls) == 1

    client.put("/api/v1/prompts/a.b", json={"body": "edited text"})
    assert client.get("/api/v1/prompts/a.b/guide").json()["what"] == "v2"
    assert len(calls) == 2


def test_every_save_and_reset_is_recorded_and_listed_newest_first(client, monkeypatch):
    from app.services import prompt_store

    log: list[dict] = []

    class _Table:
        def insert(self, row):
            log.append(row)
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    real = prompt_store._client()
    monkeypatch.setattr(
        prompt_store, "_client", lambda: SimpleNamespace(table=lambda name: _Table() if name == "agent_prompt_versions" else real.table(name))
    )
    client.put("/api/v1/prompts/a.b", json={"body": "new text", "note": "Assistant: warmer tone"})
    client.post("/api/v1/prompts/a.b/reset")
    assert [(r["body"], r["note"]) for r in log] == [
        ("new text", "Assistant: warmer tone"),
        ("default", "Went back to the original"),
    ]


def test_a_missing_history_table_never_blocks_a_save(client):
    assert client.put("/api/v1/prompts/a.b", json={"body": "new text"}).status_code == 200


def test_versions_endpoint_maps_rows_and_degrades_when_unavailable(client, monkeypatch):
    from app.services import prompt_store

    monkeypatch.setattr(
        prompt_store,
        "list_versions",
        lambda key: [{"id": 2, "body": "b", "note": "n", "created_by": "a@x.com", "created_at": "t2"}],
    )
    data = client.get("/api/v1/prompts/a.b/versions").json()
    assert data["available"] is True
    assert data["versions"] == [{"id": 2, "body": "b", "note": "n", "updatedBy": "a@x.com", "updatedAt": "t2"}]

    def boom(key):
        raise RuntimeError("no table")

    monkeypatch.setattr(prompt_store, "list_versions", boom)
    assert client.get("/api/v1/prompts/a.b/versions").json() == {"versions": [], "available": False}
