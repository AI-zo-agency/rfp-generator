import asyncio
from app.services.supabase_db import _get_client
from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft, aget_research_cache
from app.services.rfp_repository import get_rfp
from app.services.proposal_self_edit_loop import _repair_one_section

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
    draft = await aget_proposal_draft(gilroy_id)
    if not draft:
        print("No draft found")
        return
        
    rfp = get_rfp(gilroy_id)
    research = await aget_research_cache(gilroy_id)

    # Find Case Studies section ID
    section_id = None
    for s in draft.sections:
        if "case stud" in (s.title or "").lower() or "reference" in (s.title or "").lower():
            print(f"Found candidate section: {s.title} ({s.id})")
            section_id = s.id
            break

    if not section_id:
        print("No Case Studies section found")
        return

    instructions = """
Please rewrite this section to fix the following specific issues based on the editor's feedback:
1. Reframe the Umatilla example. Right now it says "every channel pointed back to a streamlined, rebuilt website" — this directly contradicts our "we don't rebuild, we optimize" pitch. Rewrite the sentence to remove the "rebuilt website" detail so it doesn't undercut the positioning. Emphasize optimizing their existing assets instead.
2. Add numbers to the Umatilla case study. "Record ticket sales in week one" and "renewed three consecutive times" are real but not quantified. If a percentage or growth stat exists in the knowledge base, pull it in. If not, explicitly add "[VERIFY: insert exact percentage increase in ticket sales]" and "[VERIFY: insert exact ROI]" to flag it for the user. We MUST have quantifiable data here.
3. Add a sponsorship-specific gap note. Neither current case study involves direct sponsorship procurement. Add an honest, transparent note acknowledging this gap (e.g., "Note: We are naming plainly that our core case studies focus on event marketing and conversion rather than direct sponsorship procurement, though our methodology directly supports sponsorship activation...").
"""
    
    print(f"Running repair on {section_id}...")
    sid, improved, detail = await _repair_one_section(
        gilroy_id,
        section_id,
        use_senior_editor=True,
        rfp=rfp,
        rfp_client=rfp.client,
        rfp_title=rfp.title,
        budget=research.budget if research else None,
        repair_message=instructions,
    )
    
    print(f"Done! Improved: {improved}, Detail: {detail}")

asyncio.run(main())
