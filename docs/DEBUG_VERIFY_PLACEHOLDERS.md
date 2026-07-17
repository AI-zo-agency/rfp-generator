# Debug: [VERIFY] Placeholders in Team Member Bios

## Problem
When generating proposals, team member bios show `[VERIFY]` placeholders for:
- Years of Experience
- Licenses & Certifications  
- Key Accounts
- Education

Even though bio files (like `04_Bio_SonjaAnderson.pdf`) exist in the knowledge base.

## Root Cause
**Two issues identified:**

### 1. Code Issue (FIXED ✅)
The original code was doing extraction from a massive 500KB text blob containing ALL team bios mixed together. The LLM had difficulty finding specific team members' information in this unstructured data, causing it to return empty arrays `[]` which triggered the `[VERIFY]` placeholders.

**Fix Applied:** Modified `/Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_sections_graph.py` to:
- Do targeted Supermemory searches for each specific team member BEFORE extraction
- Try multiple query variations (e.g., "04_Bio_ Sonja Anderson", "Sonja Anderson resume", etc.)
- Use the targeted results (up to 50KB) for extraction instead of the full 500KB blob
- Add comprehensive logging to track what's being found/not found
- Handle name variations (e.g., "Sonja M. Anderson" vs "Sonja Anderson")

### 2. Data Issue (NEEDS FIXING ❌)
The bio PDF files in Supermemory have NOT been properly indexed:

**Evidence:**
```json
{
  "fileName": "04_Bio_SonjaAnderson.pdf",
  "summary": "The provided document appears to be a broken link or a placeholder 
             for a Google Drive file, displaying a 'Loading' state...",
  "status": "done"
}
```

The PDFs were ingested from Google Drive, but Supermemory only captured the "Loading..." page instead of the actual PDF content. This means searches cannot find the bio information.

## Solution

### Immediate (Code Fix - DONE ✅)
The code now:
1. Does targeted searches for each team member
2. Logs detailed information about what it finds
3. Falls back gracefully when data isn't available
4. Provides better error messages

### Short-term (Re-ingest Bio PDFs - REQUIRED)
You need to re-ingest the bio PDFs so Supermemory can properly extract and index their content.

**Option 1: Re-sync from Google Drive**
```bash
cd /Users/mahipatel/ZO-AGENCY/backend
python3 -c "
import asyncio
from app.services import supermemory

async def trigger_sync():
    result = await supermemory.trigger_google_drive_sync()
    print('Sync triggered:', result)

asyncio.run(trigger_sync())
"
```

**Option 2: Upload Bio PDFs directly**
If the PDFs are local, use the ingestion script:
```bash
cd /Users/mahipatel/ZO-AGENCY/backend
python3 scripts/ingest_drive_folder_to_supermemory.py --folder-id <google-drive-folder-id>
```

**Option 3: Extract text from PDFs and upload as text documents**
```bash
cd /Users/mahipatel/ZO-AGENCY/backend
python3 -c "
import asyncio
from app.services import supermemory, pdf_text

async def ingest_bio(pdf_path, person_name):
    text = pdf_text.extract_text(pdf_path)
    result = await supermemory.add_text_document(
        title=f'Bio — {person_name}',
        content=text,
        metadata={
            'type': 'knowledge_base',
            'category': 'team_bio',
            'fileName': f'04_Bio_{person_name.replace(\" \", \"\")}.pdf'
        }
    )
    print(f'Ingested bio for {person_name}')

# Example: 
# asyncio.run(ingest_bio('/path/to/sonja_bio.pdf', 'Sonja Anderson'))
"
```

## Testing
After re-ingesting the PDFs, test with:

```bash
cd /Users/mahipatel/ZO-AGENCY/backend
python3 -c "
import asyncio
from app.services import supermemory

async def test():
    hits = await supermemory.search_hybrid(query='Sonja Anderson years of experience education', limit=5)
    print(f'Found {len(hits)} hits')
    for hit in hits:
        metadata = hit.get('metadata', {})
        print(f'  - {metadata.get(\"fileName\", \"unknown\")}')
        content = supermemory.hit_text(hit)[:300]
        print(f'    {content}')

asyncio.run(test())
"
```

You should see actual bio content (years of experience, education, etc.) instead of "Loading..." messages.

## Changes Made

### File: `/Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_sections_graph.py`

1. **Added import** (line ~17):
   ```python
   from app.services import llm, proposal_knowledge_base_tools, supermemory
   ```

2. **Added targeted search** (lines ~462-495):
   - Searches for each team member with 4 different query variations
   - Deduplicates results by document ID
   - Formats unique hits into focused KB text (max 50KB vs previous 500KB)
   - Falls back to general bio KB if no targeted results
   - Logs findings for debugging

3. **Enhanced extraction prompt** (line ~503):
   - Added note about name variations
   - Instructions to look for ALL name variations

4. **Added extraction logging** (lines ~528-532):
   - Logs what was/wasn't found for each team member
   - Helps diagnose why placeholders appear

## Expected Behavior After Fix

**If bio content IS properly indexed:**
- Targeted search finds the bio
- Extraction succeeds
- No `[VERIFY]` placeholders

**If bio content is NOT indexed (current state):**
- Logs show: "No targeted KB results for Sonja Anderson"
- Falls back to general bio KB (same as before)
- Still shows `[VERIFY]` placeholders
- But logs help you diagnose the problem

## Next Steps
1. ✅ Code fix is deployed
2. ❌ Re-ingest bio PDFs using one of the options above
3. ❌ Test that searches now return bio content
4. ❌ Generate a new proposal to verify placeholders are gone
