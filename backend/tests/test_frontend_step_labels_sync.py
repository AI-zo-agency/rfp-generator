"""The UI chip list must match the backend pipeline.

FULFILL_SCAN_STEP_LABELS in the frontend is maintained by hand. It had drifted to 11
chips for a 17-stage pipeline, so six stages ran invisibly — and the three added by the
review-agent work were invisible too. A stage the user cannot see cannot be trusted to
have run.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from app.services import proposal_fulfill_rfp_gaps as mod

FRONTEND = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/lib/proposal-pipeline-checkpoint.ts"
)


def _backend_steps() -> list[str]:
    src = inspect.getsource(mod._run_fulfill_rfp_gaps_body)
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if "FULFILL_STEPS = (" in ln)
    steps: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(")"):
            break
        if stripped.startswith('"'):
            steps.append(stripped.rstrip(",").strip('"'))
    return steps


def _frontend_steps() -> list[str]:
    text = FRONTEND.read_text()
    # Terminate on "] as const", not the first "]" — a label may itself contain
    # brackets ("Remove optional [VERIFY] tags"), which silently truncated this list.
    block = text.split("FULFILL_SCAN_STEP_LABELS = [", 1)[1].split("] as const", 1)[0]
    return re.findall(r'"([^"]+)"', block)


def test_frontend_file_exists():
    assert FRONTEND.exists(), f"expected {FRONTEND}"


def test_chip_labels_match_backend_steps_exactly():
    assert _frontend_steps() == _backend_steps()
