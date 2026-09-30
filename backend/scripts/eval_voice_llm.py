"""Live eval for app.services.proposal_voice_llm on a proposal fixture.

Calls the real light-tier model. Not part of the pytest suite. The default
fixture is the Contra Costa proposal; other fixtures use --generic to skip the
proposal-specific phrase lists.

    cd backend && ../.venv/bin/python scripts/eval_voice_llm.py
    cd backend && ../.venv/bin/python scripts/eval_voice_llm.py --fixture tests.fixtures.voice_llm_proposal_2 --generic
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.proposal_voice_llm import _protected, _tokens, rewrite_for_voice  # noqa: E402

# Defects the client flagged or Rev 6 bans. Each must be gone after the pass.
MUST_BE_GONE = [
    "won't claim",
    "not going to claim",
    "say plainly",
    "say that plainly",
    "demonstrate, overstating",
    "Bid that record",
    "making for the first time",
    "Mr. Edwards",
    "—",
    "which is what a contract like this one",
    "which is why we cite",
    "which is the same discipline",
    "We won't repeat that table",
    "we won't duplicate",
    "What we'll add instead",
    "We think that's a fair trade",
    "update this table",
    "Agency Director and Agency Director",
]
# Judgment calls: a pass if the model fixed it, or if the verifier blocked the edit and
# it was reported for a human. Never a silent leave-as-is.
NEEDS_HUMAN_OK = ["update this table"]
# Facts that must survive untouched.
MUST_STAY = [
    "Maricopa County",
    "Deschutes County",
    "City of Bend",
    "RFP_F-Contr-0000000289",
    "$400",
    "$275",
    "Net 30",
    "HIPAA",
    "gave us practice",  # claim strength: must not become "we unified"
    "closest match",
    "24-hour",
    "(541) 350-2778",
    "connect@zo.agency",
    "chantals@dpls.lib.or.us",
]


def numeric_tokens(text: str) -> set[str]:
    return {t for t in _tokens(text) if any(ch.isdigit() for ch in t)}


def table_shape(text: str) -> list[int]:
    return [line.count("|") for line in text.splitlines() if line.lstrip().startswith("|")]


SECTIONS: list = []


async def main(fixture: str, generic: bool) -> int:
    global SECTIONS
    SECTIONS = importlib.import_module(fixture).SECTIONS
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *(rewrite_for_voice(body, register=reg) for reg, _, body in SECTIONS)
    )
    failures: list[str] = []
    warnings: list[str] = []
    outputs: list[str] = []

    for (reg, title, body), res in zip(SECTIONS, results):
        print(f"\n{'=' * 78}\n{title}  [{reg}]  model={res.model or '-'}")
        if res.skipped:
            print(f"  SKIPPED: {res.skipped}")
            failures.append(f"{title}: skipped ({res.skipped})")
        for e in res.applied:
            print(f"  [{e.rule}]\n    - {e.find}\n    + {e.replace or '(deleted)'}")
        for e, why in res.rejected:
            print(f"  REJECTED ({why}): {e.find[:90]!r}")
        for e in res.suggested:
            print(f"  suggested (not applied) [{e.rule}]\n    - {e.find}\n    ? {e.replace or '(deleted)'}")
        if not res.applied and not res.rejected and not res.suggested:
            print("  no edits")
        outputs.append(res.text)

        if table_shape(res.text) != table_shape(body):
            failures.append(f"{title}: table shape changed")
        if numeric_tokens(res.text) != numeric_tokens(body):
            lost = numeric_tokens(body) - numeric_tokens(res.text)
            new = numeric_tokens(res.text) - numeric_tokens(body)
            failures.append(f"{title}: numbers changed, lost={sorted(lost)} new={sorted(new)}")

    combined = "\n".join(outputs)
    print(f"\n{'=' * 78}\nCHECKS  (pass 1 took {time.perf_counter() - t0:.0f}s)")
    if not generic:
        reported = [edit.find for res in results for edit, _ in res.rejected]
        for phrase in MUST_BE_GONE:
            if phrase.casefold() in combined.casefold():
                if phrase in NEEDS_HUMAN_OK and any(phrase in f for f in reported):
                    print(f"  needs human review (reported, not silent): {phrase!r}")
                    continue
                failures.append(f"still present: {phrase!r}")
        for phrase in MUST_STAY:
            if phrase not in combined:
                failures.append(f"fact lost: {phrase!r}")
    bad = [l for l in combined.splitlines() if "—" in l and not l.lstrip().startswith("#") and not _protected(l)]
    if bad:
        failures.append(f"em dash still present in {len(bad)} line(s): {bad[0][:80]!r}")

    # Idempotency: a second pass over the output should have nothing left to fix.
    second = await asyncio.gather(
        *(rewrite_for_voice(out, register=reg) for (reg, _, _), out in zip(SECTIONS, outputs))
    )
    for (_, title, _), res in zip(SECTIONS, second):
        if res.applied:
            warnings.append(f"{title}: second pass still applies {len(res.applied)} spans")
            for e in res.applied:
                print(f"  2nd pass [{e.rule}] {e.find[:90]!r}")

    print(f"\nFINAL TEXT\n{'=' * 78}")
    for (_, title, _), out in zip(SECTIONS, outputs):
        print(f"\n## {title}\n{out}")

    print(f"\n{'=' * 78}")
    if warnings:
        print(f"WARN, not scored: an LLM pass is not idempotent ({len(warnings)} sections)")
        for w in warnings:
            print(f"  - {w}")
    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="tests.fixtures.voice_llm_proposal")
    ap.add_argument("--generic", action="store_true", help="skip proposal-specific phrase lists")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.fixture, args.generic)))
