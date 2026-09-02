import asyncio
from app.services.supabase_db import _get_client
from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
from datetime import datetime, timezone

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
        if s.id == "rfp-structure-case-studies":
            s.content = """Gilroy's festival already has a rebuilt website and a strong brand. What you need next is a partner who can scale ticket sales, sponsor visibility, and audience reach from what's already built, not start over. Two of our case studies show exactly that.

| Client | Challenge | What We Did |
|---|---|---|
| City of Umatilla, "Rock the Locks" Festival | The festival had a tight runway to drive awareness and ticket sales, with no room for a slow build and no increase in budget. | We launched a coordinated campaign across email, social, print, radio, TV, digital, and PR within days. Every channel pointed back to one clear, high-converting path to purchase, with one consistent message delivered at high frequency. |
| Oregon Employment Department | The department needed to reach unemployed individuals statewide, a notoriously hard audience to target through standard digital methods. | We built a precision geofencing system on top of their existing digital infrastructure and expanded their brand guidelines to support hundreds of ad variations tailored to different regions and audiences. |

Rock the Locks turned that coordinated push into record ticket sales in week one, ahead of the festival's entire previous year, without spending more to get there. Oregon Employment Department's results held up well enough that the client renewed our contract three consecutive times.

For Gilroy, that's the same playbook: take the website, brand, and channels you've already invested in, and get more ticket sales, sponsor visibility, and attendee engagement out of them without rebuilding what already works.

Note: Neither case study above involves sponsorship development specifically — our sponsorship approach for Gilroy draws on our broader experience with tiered stakeholder programs and municipal partnership structures, applied fresh to this engagement."""
            print("Fixed Case Studies")

    draft = draft.model_copy(update={"updated_at": datetime.now(timezone.utc).isoformat()})
    await asave_proposal_draft(draft)
    print("Saved draft")

asyncio.run(main())
