"""Capacity invariant: adding stages must never displace an existing one.

The Complete & Scan pipeline is additive by constraint. Every stage that ran before this
work must still run, in the same relative order, with new stages appended — not
substituted. A silently dropped stage is a capability loss that no other test would
notice, because each stage is individually best-effort and logs its own skip.
"""

from __future__ import annotations

import inspect
import re

from app.services import proposal_fulfill_rfp_gaps as mod

# Core stages that must remain present (order matches FULFILL_STEPS).
ORIGINAL_STEPS = [
    "RFP structure (all scored sections)",
    "Closing & submission tabs",
    "Requirement ledger (merge / cut / add)",
    "DQ & gov-policy gate (agentic loop)",
    "Remove duplicate sections",
    "Senior editor review (RFP reviewer)",
    "Budget (regen if missing + thorough)",
    "Consistency repairs",
    "Compliance fabrication guard",
    "Contractor KPIs (Section 2.3)",
    "KB fact-check (Supermemory)",
    "RFP contradiction check (LLM)",
    "Line-by-line KB grounding (async)",
    "Remove optional VERIFY/MANUAL FILL",
    "Compact manuscript (remove duplicates)",
    "Page limit & anti-invention (Ralph)",
    "Pre-submit refresh",
]


def _current_steps() -> list[str]:
    """FULFILL_STEPS is a local inside the fulfill coroutine, so read it from source."""
    src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if "FULFILL_STEPS = (" in ln)
    steps: list[str] = []
    # Stop at the tuple's own closing paren; step labels themselves contain parens
    # ("RFP structure (all scored sections)"), so a plain index(")") ends far too early.
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(")"):
            break
        if stripped.startswith('"'):
            steps.append(stripped.rstrip(",").strip('"'))
    return steps


def test_every_original_step_still_present():
    current = _current_steps()
    missing = [s for s in ORIGINAL_STEPS if s not in current]
    assert not missing, f"stages dropped: {missing}"


def test_original_steps_keep_their_relative_order():
    current = _current_steps()
    positions = [current.index(s) for s in ORIGINAL_STEPS]
    assert positions == sorted(positions), "original stages were reordered"


def test_pre_submit_refresh_still_precedes_new_stages():
    """Pre-submit must still see the finished manuscript before readiness reports on it."""
    current = _current_steps()
    assert current.index("Pre-submit refresh") > current.index(
        "Page limit & anti-invention (Ralph)"
    )


def test_readiness_stage_is_registered():
    assert "Submission readiness (triage + score)" in _current_steps()


def test_step_total_matches_the_tuple():
    """step_total is derived, so progress cannot silently drift from reality."""
    src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
    assert "step_total=len(FULFILL_STEPS)" in src


def test_no_duplicate_step_labels():
    current = _current_steps()
    assert len(current) == len(set(current))


def test_no_standalone_repetition_sweep_stage():
    """Removed: the pipeline already dedupes three times.

    "Remove duplicate sections", "Compact manuscript", and the gate's own repetition
    detector all cut duplicates. A fourth pass cost a full LLM sweep of the manuscript
    to re-answer a question three other stages ask, and showed the user a chip for work
    that mostly found nothing.
    """
    assert "Repetition sweep (whole manuscript)" not in _current_steps()


def test_pipeline_starts_with_structure():
    assert _current_steps()[0] == "RFP structure (all scored sections)"


def test_quality_gate_removed_from_scan():
    """Former 3-act quality gate is gone — final readiness verifies for designer."""
    current = _current_steps()
    assert "Review & quality gate (3 acts)" not in current
    assert current.index("Pre-submit refresh") < current.index(
        "Submission readiness (triage + score)"
    )
    assert current[-1] == "Submission readiness (triage + score)"


def _progress_indices() -> list[int]:
    """Every literal step index passed to _scan_progress, in source order."""
    src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
    lines = src.splitlines()
    out: list[int] = []
    for i, line in enumerate(lines):
        inline = re.match(r"\s*await _scan_progress\((\d+),", line)
        if inline:
            out.append(int(inline.group(1)))
            continue
        if line.rstrip().endswith("await _scan_progress(") and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if re.fullmatch(r"\d+,", nxt):
                out.append(int(nxt.rstrip(",")))
    return out


def test_progress_indices_are_in_range():
    """A step index past the end of the tuple makes the progress bar lie."""
    total = len(_current_steps())
    indices = _progress_indices()
    assert indices, "no _scan_progress calls found — the parser broke"
    assert min(indices) >= 1
    assert max(indices) <= total, f"index {max(indices)} exceeds {total} steps"


def test_progress_indices_never_move_backwards():
    """Renumbering errors show up as a progress bar that jumps backwards."""
    indices = _progress_indices()
    assert indices == sorted(indices), f"indices go backwards: {indices}"


def test_first_and_last_stage_indices_are_wired():
    indices = _progress_indices()
    assert indices[0] == 1
    assert max(indices) == len(_current_steps())
