import asyncio
from app.services.supabase_db import _get_client
from app.services.proposal_repository import aget_proposal_draft

async def main():
    sb = _get_client()
    res = sb.table("rfps").select("id, title").execute()
    gilroy_id = None
    for row in res.data:
        if "gilroy" in row["title"].lower():
            gilroy_id = row["id"]
            break

    draft = await aget_proposal_draft(gilroy_id)
    for s in draft.sections:
        if any(x in (s.title or "").lower() for x in ["case stud", "optimiz", "budget"]):
            print(f"--- SECTION: {s.title} ---")
            print(s.content)
            print("-" * 50)

asyncio.run(main())
