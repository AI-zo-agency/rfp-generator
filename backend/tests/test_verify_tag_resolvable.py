"""Every emitted [VERIFY] tag must be matchable by the canonical resolver regex.

A bare "[VERIFY]" (no colon) is not matched by VERIFY_TAG_RE, which is what the
optional scrubber, the section editor's KB fill-in and the budget content pass
all use. Such a tag can never be filled from the KB nor removed when the RFP
does not require the fact — it reaches the exported document unconditionally.
"""

import ast
import re
from pathlib import Path

from app.services.proposal_manual_flags import VERIFY_TAG_RE
from app.services.proposal_sections_graph import _sanitize_bio_extraction

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_bio_expertise_years_tag_is_resolvable():
    kb_text = "Sonja Anderson\nEXPERTISE\nBrand strategy for public agencies.\n"
    extracted = {"expertise": [{"area": "Brand strategy", "years": ""}]}

    clean = _sanitize_bio_extraction(extracted, kb_text)
    years = clean["expertise"][0]["years"]

    assert "[VERIFY" in years, "expected a verify tag when years are unknown"
    assert VERIFY_TAG_RE.search(years), (
        f"tag {years!r} is not matched by VERIFY_TAG_RE — resolvers cannot fill "
        "or scrub it"
    )


def test_known_years_are_left_alone():
    kb_text = "Sonja Anderson\nEXPERTISE\nBrand strategy for public agencies.\n"
    extracted = {"expertise": [{"area": "Brand strategy", "years": "14"}]}

    clean = _sanitize_bio_extraction(extracted, kb_text)
    assert clean["expertise"][0]["years"] == "14"


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id() of every string Constant used as a docstring."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None) or []
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.add(id(body[0].value))
    return out


# Files whose "[VERIFY]" literals are log/telemetry text, never proposal content.
_NON_CONTENT_MODULES = {"sections_agent_log.py"}


def test_no_source_file_emits_a_bare_verify_tag_into_content():
    """Guard the whole codebase, not just the one site fixed.

    Uses the AST so comments and docstrings that merely *mention* the bare form
    (including this fix's own explanatory comment) are not false positives.
    """
    offenders: list[str] = []

    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or path.name in _NON_CONTENT_MODULES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            if re.fullmatch(r"\s*\[VERIFY\]\s*", node.value):
                offenders.append(
                    f"{path.relative_to(APP_DIR.parent)}:{node.lineno}"
                )

    assert not offenders, (
        "bare '[VERIFY]' string literal emitted at: "
        + ", ".join(offenders)
        + " — use the colon form '[VERIFY: <field>]' so resolvers can match it"
    )
