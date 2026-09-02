import asyncio
import json
from app.services.supabase_db import _get_client
from app.services.proposal_repository import aget_research_cache

async def main():
    sb = _get_client()
    res = sb.table("rfps").select("id, title").execute()
    gilroy_id = None
    for row in res.data:
        if "gilroy" in row["title"].lower():
            gilroy_id = row["id"]
            break
            
    if not gilroy_id:
        return
        
    research = await aget_research_cache(gilroy_id)
    if research and research.budget and research.budget.line_items:
        for item in research.budget.line_items:
            print(f"Category: {repr(item.category)} | Desc: {repr(item.description)}")

asyncio.run(main())
