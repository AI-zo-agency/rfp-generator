"""Shadow-run the pricing plan on stored RFPs; never persists.

Usage (from backend/):
  PYTHONPATH=. ../.venv/bin/python scripts/pricing_plan_shadow.py OUT_DIR --stop-at-usd 45 RFP_ID [RFP_ID ...]
  add  --local-docs DIR  to price from the three pricing docs in DIR instead of the Knowledge Base
       (01_Pricing_Book.md, 02_Rules_and_Wording.md, 03_Pricing_Internal.md); nothing is uploaded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services import pricing_plan_service as svc
from app.services.go_no_go_service import combine_rfp_text
from app.services.monthly_llm_budget import get_monthly_budget_status
from app.services.pricing_kb import build_book
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_common import load_rfp_for_proposal
from app.services.proposal_repository import aget_proposal_draft, aget_research_cache


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("rfp_ids", nargs="+")
    ap.add_argument("--stop-at-usd", type=float, required=True)
    ap.add_argument("--local-docs", type=Path, default=None)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.local_docs:
        book = build_book(*[(args.local_docs / n).read_text() for n in
                            ("01_Pricing_Book.md", "02_Rules_and_Wording.md", "03_Pricing_Internal.md")])

        async def local_book():
            return book

        svc.load_pricing_kb = local_book  # type: ignore[assignment]
    summary = []
    for rfp_id in args.rfp_ids:  # sequential so the spend check is exact
        spent = float(get_monthly_budget_status(use_cache=False).get("spent_usd") or 0)
        if spent >= args.stop_at_usd:
            print(f"stop: monthly spend ${spent:.2f} >= ${args.stop_at_usd}")
            break
        _rfp, content, _ctx = load_rfp_for_proposal(rfp_id)
        research = await aget_research_cache(rfp_id)
        budget = await svc.generate_pricing_plan_budget(
            rfp_id,
            combine_rfp_text(content.description, content.pdf_text),
            target_budget_usd=research.target_budget_usd if research else None,
        )
        draft = await aget_proposal_draft(rfp_id)
        idx = find_budget_section_index(draft.sections) if draft else None
        plan = budget.pricing_plan or {}
        (args.out / f"{rfp_id}.v1.md").write_text(draft.sections[idx].content if idx is not None else "(no v1 Cost section)")
        (args.out / f"{rfp_id}.v2.md").write_text(svc.render_pricing_plan_budget(budget))
        (args.out / f"{rfp_id}.v2.internal.md").write_text(plan.get("internal_summary", ""))
        (args.out / f"{rfp_id}.v2.plan.json").write_text(json.dumps(plan, indent=1, default=str))
        summary.append({
            "rfp_id": rfp_id,
            "pricing_version": plan.get("pricing_version"),
            "priced_total": sum(li.extended or 0 for li in budget.line_items),
            "manual_rows": sum(1 for li in budget.line_items if li.is_manual_fill),
            "flags": budget.pricing_flags,
        })
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary[-1], indent=1))


if __name__ == "__main__":
    asyncio.run(main())
