import asyncio
from app.services.supabase_db import _get_client
from app.services.proposal_repository import aget_research_cache, asave_proposal_draft
from app.services.proposal_budget_content import (
    incorporate_budget_into_draft,
    find_budget_section_index,
    _mix_table_from_line_items,
)

_BUDGET_ATTACHMENT_NOTE = "\n\n> **RFP budget file (required with proposal):** Action needed — attach completed budget worksheet per RFP instructions before export.\n"

async def main():
    sb = _get_client()
    res = sb.table("rfps").select("id, title").execute()
    gilroy_id = None
    for row in res.data:
        if "gilroy" in row["title"].lower():
            gilroy_id = row["id"]
            break

    if not gilroy_id:
        print("Gilroy RFP not found")
        return

    print(f"Found Gilroy ID: {gilroy_id}")
    research = await aget_research_cache(gilroy_id)
    if not research or not research.budget:
        print("Missing research or budget")
        return

    # Test the Component table rendering with the fixed code
    budget = research.budget
    total = sum(
        float(item.extended)
        for item in (budget.line_items or [])
        if isinstance(getattr(item, "extended", None), (int, float)) and float(item.extended) > 0
    )
    table = _mix_table_from_line_items(budget.line_items, total)
    print("=== New Component Table ===")
    print(table)
    print()

    # Now regenerate the full budget section
    new_draft = await incorporate_budget_into_draft(gilroy_id, research.budget)
    if new_draft:
        # Restore budget worksheet flag
        idx = find_budget_section_index(new_draft.sections)
        if idx is not None:
            content = new_draft.sections[idx].content or ""
            if "attach completed budget worksheet" not in content:
                content = content.rstrip() + _BUDGET_ATTACHMENT_NOTE
                new_draft.sections[idx] = new_draft.sections[idx].model_copy(update={"content": content})
                print("Restored budget attachment flag.")

        await asave_proposal_draft(new_draft)
        print("Regeneration complete!")
    else:
        print("Failed to rebuild draft.")

asyncio.run(main())
