#!/usr/bin/env python3
"""
Test the knowledge base queries that the proposal generation system uses
to fetch team member bios/resumes.

This shows you exactly what the LLM will see when extracting resume information.
"""

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services import supermemory


async def test_member_queries(member_name: str):
    """Test the 4 query variations used to find a team member's bio."""
    
    print(f"\n{'='*80}")
    print(f"TESTING QUERIES FOR: {member_name}")
    print(f"{'='*80}\n")
    
    # These are the exact queries used in proposal_sections_graph.py
    member_queries = [
        f"04_Bio_ {member_name}",
        f"zö agency team bio {member_name}",
        f"{member_name} resume professional experience",
        f"{member_name} zo agency",
    ]
    
    all_hits = []
    
    for i, query in enumerate(member_queries, 1):
        print(f"\n--- Query {i}/4: '{query}' ---")
        
        # Use v4/search with hybrid mode (same as production code)
        hits = await supermemory.search_documents(
            query=query,
            limit=10,
            include_full_docs=True,
            search_mode="hybrid"
        )
        print(f"Found: {len(hits)} v4/search results (hybrid mode with full docs)")
        
        for j, hit in enumerate(hits[:3], 1):  # Show top 3 hits
            metadata = hit.get('metadata', {})
            fname = metadata.get('fileName', 'unknown')
            content = supermemory.hit_text(hit)
            
            print(f"\n  Hit {j}: {fname}")
            print(f"  Content length: {len(content)} chars")
            
            # Check for key resume sections
            checks = {
                'Has YEARS OF EXPERIENCE': 'YEARS OF EXPERIENCE' in content or 'years' in content.lower(),
                'Has EDUCATION': 'EDUCATION' in content or 'Associate' in content or 'Bachelor' in content,
                'Has KEY ACCOUNTS': 'KEY ACCOUNTS' in content or 'ACCOUNTS' in content,
                'Has WORK HISTORY': 'WORK HISTORY' in content or 'Founder' in content or 'Director' in content,
                'Has name': member_name.split()[0] in content or member_name in content,
            }
            
            for check, result in checks.items():
                status = '✅' if result else '❌'
                print(f"  {status} {check}")
            
            # Show content preview
            print(f"\n  Content preview (first 400 chars):")
            print(f"  {'-'*76}")
            preview = content[:400].replace('\n', '\n  ')
            print(f"  {preview}...")
            print(f"  {'-'*76}")
        
        all_hits.extend(hits)
    
    # Summary
    print(f"\n\n{'='*80}")
    print(f"SUMMARY FOR {member_name}")
    print(f"{'='*80}")
    print(f"Total unique chunks found: {len(all_hits)}")
    
    # Deduplicate and merge
    seen_ids = set()
    unique_hits = []
    for hit in all_hits:
        hit_id = hit.get("id") or hit.get("customId")
        if hit_id and hit_id not in seen_ids:
            seen_ids.add(hit_id)
            unique_hits.append(hit)
    
    print(f"Unique chunks after deduplication: {len(unique_hits)}")
    
    # Format as the LLM will see it
    merged_text = supermemory.format_search_hits(unique_hits, max_chars=50000)
    print(f"Total text for LLM extraction: {len(merged_text)} chars")
    
    # Show what the LLM will receive
    print(f"\nWhat the LLM will see (first 1000 chars):")
    print(f"{'-'*80}")
    print(merged_text[:1000])
    print(f"...\n{'-'*80}")
    
    # Analysis
    has_useful_content = len(merged_text) > 500
    has_bio_file = any('04_Bio' in hit.get('metadata', {}).get('fileName', '') for hit in unique_hits)
    
    print(f"\n{'='*80}")
    print(f"ANALYSIS")
    print(f"{'='*80}")
    
    if has_useful_content:
        print(f"✅ Found sufficient content ({len(merged_text)} chars)")
    else:
        print(f"❌ Insufficient content ({len(merged_text)} chars) - may show [VERIFY] placeholders")
    
    if has_bio_file:
        print(f"✅ Found dedicated bio file (04_Bio_*.pdf)")
    else:
        print(f"⚠️  No dedicated bio file found - using content from proposals")
    
    print()


async def main():
    if not supermemory.is_configured():
        print("ERROR: SUPERMEMORY_API_KEY not configured in .env")
        return 1
    
    # Test with different team members
    test_members = [
        "Sonja Anderson",
        "Rachael Rice",
        "Todd Anderson",
    ]
    
    print("\n" + "="*80)
    print("RESUME QUERY TEST - Shows what the LLM sees when extracting bios")
    print("="*80)
    
    for member in test_members:
        await test_member_queries(member)
        print("\n" + "="*80 + "\n")
    
    print("\n✅ Test complete!")
    print("\nNEXT STEPS:")
    print("  1. If you see ❌ Insufficient content - the bio file may not be properly indexed")
    print("  2. If you see ⚠️ No dedicated bio file - re-ingest the bio PDFs")
    print("  3. If content looks good - generate a proposal and check for [VERIFY] tags")
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
