import asyncio
import re
from app.services.proposal_repository import list_proposal_draft_summaries, asave_proposal_draft, aget_proposal_draft

async def run_retroactive_cleanup():
    """
    Surgically removes known hallucinations from all existing drafts in the database.
    Costs $0 in tokens and runs instantly since it uses direct regex/string replacement.
    """
    summaries = list_proposal_draft_summaries()
    
    updated_count = 0
    for s in summaries:
        rfp_id = s.get("id") or s.get("rfp_id")
        draft = await aget_proposal_draft(rfp_id)
        if not draft:
            continue
            
        made_changes = False
        
        for section in draft.sections:
            original_content = section.content
            
            # 1. Fix the case study hallucination
            if "regional transit points" in section.content:
                section.content = section.content.replace(
                    "regional transit points", 
                    "regional airport"
                )
                made_changes = True
                
            # 2. Excise "City of Northglenn" from client lists
            if "Northglenn" in section.content:
                # Catch "City of Northglenn, "
                section.content = re.sub(r'(?:the\s+)?City of Northglenn,\s*and\s+', '', section.content, flags=re.IGNORECASE)
                section.content = re.sub(r'(?:the\s+)?City of Northglenn,\s*', '', section.content, flags=re.IGNORECASE)
                # Catch ", City of Northglenn"
                section.content = re.sub(r',\s*and\s+(?:the\s+)?City of Northglenn', '', section.content, flags=re.IGNORECASE)
                section.content = re.sub(r',\s*(?:the\s+)?City of Northglenn', '', section.content, flags=re.IGNORECASE)
                # Catch isolated mentions
                section.content = re.sub(r'(?:the\s+)?City of Northglenn', '', section.content, flags=re.IGNORECASE)
                
                # In case they just said "Northglenn"
                section.content = re.sub(r',\s*and\s+Northglenn', '', section.content, flags=re.IGNORECASE)
                section.content = re.sub(r'Northglenn,\s*and\s+', '', section.content, flags=re.IGNORECASE)
                section.content = re.sub(r',\s*Northglenn', '', section.content, flags=re.IGNORECASE)
                section.content = re.sub(r'Northglenn,\s*', '', section.content, flags=re.IGNORECASE)
                
                made_changes = True

        if made_changes:
            print(f"Fixed hallucinations in RFP Draft: {rfp_id}")
            await asave_proposal_draft(draft)
            updated_count += 1
            
    print(f"\\nDone! Successfully cleaned {updated_count} drafts for $0.00.")

if __name__ == "__main__":
    asyncio.run(run_retroactive_cleanup())
