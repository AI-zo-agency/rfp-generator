"""In-memory stand-in for the slice of supabase-py the revisions code uses."""

from types import SimpleNamespace


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._filter = self._insert = self._upsert = self._limit = self._order = None

    def select(self, *_):
        return self

    def eq(self, key, value):
        self._filter = (key, value)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, key, desc=False):
        self._order = (key, desc)
        return self

    def insert(self, row):
        self._insert = row
        return self

    def upsert(self, row, on_conflict=None):
        self._upsert = row
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self._insert is not None:
            row = {"id": f"id{len(rows) + 1}", "created_at": f"2026-09-30T00:00:{len(rows):02d}+00:00", **self._insert}
            rows.append(row)
            return SimpleNamespace(data=[row])
        if self._upsert is not None:
            rows[:] = [dict(self._upsert)]
            return SimpleNamespace(data=list(rows))
        out = [r for r in rows if not self._filter or r.get(self._filter[0]) == self._filter[1]]
        if self._order:
            out.sort(key=lambda r: r[self._order[0]], reverse=self._order[1])
        return SimpleNamespace(data=out[: self._limit] if self._limit else out)


class FakeDb:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Query(self, name)
