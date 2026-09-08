"""Static regression guards for the Supabase egress cleanup.

These don't test behavior — they fail loudly when a change reintroduces the
exact patterns that caused the original egress blowup risk:
  1. A new unscoped `select("*")` (full-row fetch) appearing anywhere in app/.
  2. A single-row Supabase getter (get_rfp, get_proposal_draft, etc.) called
     from inside a loop body — the N+1 pattern found in llm_cost.py and
     proposals.py's jobs/active endpoint.

A failure here does not mean the new code is wrong — it means it's the kind
of pattern that has silently caused Supabase egress problems before, and
needs a conscious look (batch it, or bump the baseline here with a comment
explaining why this one is fine).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Baseline as of the egress cleanup — every current call site was reviewed.
# Bump this only after consciously deciding a new select("*") is justified
# (e.g. a detail view that genuinely needs the whole row).
_SELECT_STAR_BASELINE = 27

# Single-row getters that must never be called per-iteration inside a loop —
# batch via get_rfps_by_ids()/an IN-query instead. Add to this list as new
# single-row getters appear in supabase_db.py / rfp_repository.py.
_SINGLE_ROW_GETTERS = {
    "get_rfp",
    "get_proposal_draft",
    "get_research_cache",
    "get_rfp_pdf_path",
}


def _iter_py_files():
    for path in APP_ROOT.rglob("*.py"):
        yield path


def test_select_star_count_has_not_grown():
    total = 0
    hits: list[str] = []
    for path in _iter_py_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        count = text.count('.select("*")')
        if count:
            total += count
            hits.append(f"{path.relative_to(APP_ROOT.parent)}: {count}")

    assert total <= _SELECT_STAR_BASELINE, (
        f"select(\"*\") call count grew from {_SELECT_STAR_BASELINE} to {total}. "
        "Each full-row fetch pulls every column (including large JSON/text "
        "blobs) over the wire even when the caller only needs a few fields. "
        "Scope the new call to specific columns, or if a full row is "
        "genuinely required, bump _SELECT_STAR_BASELINE here with a comment "
        "explaining why.\n" + "\n".join(hits)
    )


class _LoopGetterVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []
        self._loop_depth = 0

    def _visit_loop(self, node) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        # A helper function defined inside a loop body is its own scope —
        # calling get_rfp() once inside a *definition* isn't the N+1 pattern.
        outer_depth = self._loop_depth
        self._loop_depth = 0
        self.generic_visit(node)
        self._loop_depth = outer_depth

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._loop_depth > 0 and isinstance(node.func, ast.Name):
            if node.func.id in _SINGLE_ROW_GETTERS:
                self.violations.append(f"{self.filename}:{node.lineno}: {node.func.id}(...) inside a loop")
        self.generic_visit(node)


def test_no_single_row_getter_calls_inside_a_loop():
    violations: list[str] = []
    for path in _iter_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except SyntaxError:
            continue
        visitor = _LoopGetterVisitor(str(path.relative_to(APP_ROOT.parent)))
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert not violations, (
        "Found single-row Supabase getter(s) called inside a loop — this is "
        "the N+1 pattern that turned one logical request into one Supabase "
        "call per item (see llm_cost.py:_attach_titles and "
        "proposals.py:list_active_proposal_jobs_endpoint before the fix). "
        "Collect the ids first and batch-fetch with get_rfps_by_ids() (or "
        "add an equivalent batch getter) instead.\n" + "\n".join(violations)
    )
