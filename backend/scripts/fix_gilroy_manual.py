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

    if not gilroy_id:
        print("Gilroy RFP not found")
        return

    print(f"Found Gilroy ID: {gilroy_id}")
    draft = await aget_proposal_draft(gilroy_id)
    if not draft:
        print("No draft found")
        return

    for s in draft.sections:
        # Case Studies
        if s.id == "rfp-structure-case-studies":
            old = s.content or ""
            new = old.replace(
                "Every channel pointed back to a streamlined, rebuilt website designed to convert traffic into ticket sales",
                "Every channel pointed back to one clear, high-converting path to purchase"
            )
            if "Neither case study above involves sponsorship development" not in new:
                new += "\n\nNote: Neither case study above involves sponsorship development specifically — our sponsorship approach for Gilroy draws on our broader experience with tiered stakeholder programs and municipal partnership structures, applied fresh to this engagement."
            s.content = new
            print("Fixed Case Studies")

        # Approach / Optimization row
        if s.id == "rfp-structure-strategic-growth-approach":
            old = s.content or ""
            target = "Website conversion and security tuning for ticket, merchandise, and vendor-application flows;"
            replacement = "Website conversion and security tuning for ticket, merchandise, and vendor-application flows **[VERIFY: Sonja/team to confirm if website maintenance is a one-time setup task or a recurring service through the full 11-month term]**;"
            new = old.replace(target, replacement)
            s.content = new
            print("Fixed Strategic Growth Approach")

        # Budget
        if s.id == "rfp-structure-budget-cost-breakdown":
            old = s.content or ""
            
            # Remove pricing tier note
            new = old.replace(
                "- Pricing reflects Average-tier positioning: a standard nonprofit/cultural-event engagement with moderate budget and strong sector fit, not a discount or premium bid.\n",
                ""
            )
            
            # Remove garbled cell if present (from earlier version)
            new = new.replace("Phase 1 Discovery & Transition — Phase 1 Discovery & Transition —", "Discovery & Transition —")
            new = new.replace("Full audit of rebuilt w…", "Full audit of rebuilt website, social channels, and inventory of existing sponsor assets and archival media")
            new = new.replace("Phase 3 Sustained Execution — Pre-event promotional campaign anchored on Natio", "Pre-event promotional campaign anchored on National Garlic Day")

            # Add VERIFY for maintenance
            if "[VERIFY: If §22 confirms maintenance runs the full term" not in new:
                new += "\n\n**[VERIFY: If §22 (Optimization & Systems Setup) confirms maintenance runs the full term, add a recurring maintenance line item to this budget.]**"
                
            # Replace bottom flag
            flag_old = "> **RFP budget file (required with proposal):** Action needed — attach completed budget worksheet per RFP instructions before export."
            flag_new = "> **RFP budget file (required with proposal):** [MANUAL FILL: attach completed budget worksheet per RFP instructions before export.]"
            new = new.replace(flag_old, flag_new)
            
            s.content = new
            print("Fixed Budget")

    draft = draft.model_copy(update={"updated_at": datetime.now(timezone.utc).isoformat()})
    await asave_proposal_draft(draft)
    print("Saved draft")

asyncio.run(main())
