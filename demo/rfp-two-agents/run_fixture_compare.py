#!/usr/bin/env python3
"""Run Agent 1 on RFP_FIXTURE_PDF and print before/after quality metrics.

  cd demo/rfp-two-agents
  RFP_FIXTURE_PDF=/path/to/RFQ13180.pdf ../../.venv/bin/python run_fixture_compare.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO_ROOT.parents[1] / "backend"))
sys.path.insert(0, str(DEMO_ROOT))

from agent1_tools import RfpDoc, extract_opportunity_with_tools  # noqa: E402
from tests.san_diego_assertions import assert_san_diego_opportunity  # noqa: E402


async def main() -> None:
    pdf = os.environ.get("RFP_FIXTURE_PDF", "").strip()
    if not pdf or not Path(pdf).is_file():
        print("Set RFP_FIXTURE_PDF to a readable PDF path.")
        sys.exit(1)
    prior_path = os.environ.get("RFP_PRIOR_JSON", "").strip()
    prior = json.loads(Path(prior_path).read_text()) if prior_path and Path(prior_path).is_file() else {}

    doc = RfpDoc.from_pdf_bytes(Path(pdf).read_bytes(), filename=Path(pdf).name)
    prompt = (DEMO_ROOT / "prompts" / "agent1_opportunity_system.txt").read_text()
    out, trace, prov = await extract_opportunity_with_tools(
        doc=doc,
        system_prompt=prompt,
        rfp_meta={"title": Path(pdf).stem},
    )

    def metrics(d: dict) -> dict:
        comp = (d.get("compliance") or {}).get("items") or []
        scope = d.get("scope") or {}
        post = sum(
            1
            for i in comp
            if isinstance(i, dict)
            and i.get("mandatory") is False
            and "post" in str(i.get("targetSection") or "").casefold()
        )
        return {
            "compliance_items": len(comp),
            "scope_mandatory": len(scope.get("mandatory") or []),
            "scope_optional": len(scope.get("optional") or []),
            "post_award_conditional": post,
            "provenance": len(d.get("provenance") or prov),
            "scored": (d.get("evaluation") or {}).get("scoredResponseForm"),
        }

    print("=== METRICS (prior) ===", json.dumps(metrics(prior), indent=2) if prior else "{}")
    print("=== METRICS (new) ===", json.dumps(metrics(out), indent=2))
    fails = assert_san_diego_opportunity(out)
    print("=== SAN DIEGO ASSERTIONS ===", f"{len(fails)} failures")
    for f in fails:
        print(" -", f)
    out_path = DEMO_ROOT / "fixture_output.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Wrote", out_path)


if __name__ == "__main__":
    asyncio.run(main())
