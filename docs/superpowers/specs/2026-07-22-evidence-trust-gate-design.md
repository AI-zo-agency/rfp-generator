# Evidence Trust Gate — Design

**Date:** 2026-07-22  
**Status:** Approved for implementation

## Problem

Proposal generation retrieves “closest” KB neighbors and sometimes invents structured content when slots are empty. Failure modes:

1. Semantic-near but wrong work type (brand/PR cited as website; MCI tourism cited when RFP excludes MCI)
2. `Public: Confirm` clients cited as settled fact (e.g. Thrive Guides)
3. Empty refs/certs → plausible invented names/emails
4. RFP hard facts (ceiling, eval weights) missed when chunking truncates
5. `07_FIN` / competitor FOIA cited as won experience

## Decisions

- Empty slot: **Option A** — `[VERIFY]` / `[FLAG]` with explicit reason; draft continues; best-effort KB fetch first
- Gates: **retrieval + post-draft claim validator**
- Client registry source: **`01_ClientList_Approved.md`** (Verified Facts in Supermemory)
- Approach: shared Evidence Trust Gate module (not prompt-only)

## Architecture

```
RFP + section intent
  → Best-effort KB fetch
  → Evidence Trust Gate (Confirm / provenance / claim↔work-type)
  → Writer (RFP requirements only) OR VERIFY/FLAG + reason
  → Post-draft Claim Validator
RFP hard facts (full text) → Stage 1 + proposal context
```

## Modules

| Unit | Responsibility |
|------|----------------|
| `client_list_registry` | Parse/cache `01_ClientList_Approved.md` |
| `evidence_trust_gate` | Filter hits; emit gap reasons |
| `empty_slot_flags` | VERIFY/FLAG strings with why-not-found |
| `claim_validator` | Post-draft strip/flag inventions & mismatches |
| `rfp_hard_facts` | Shared ceiling + eval extraction (Go/No-Go + proposals) |

## Gate rules

1. **Public: Confirm** → never settled citation; FLAG; try another public client or VERIFY
2. **Win provenance** → `03_CS` / `06_WON` for wins; `07_FIN` labeled finalist only; `08`/competitor/Resonance never as zö win
3. **Claim ↔ work type** → ClientList tags must contain the claimed work type
4. **RFP stickiness** → answer stated requirements only; KB pricing anchors labeled as analogous
5. **Empty after best-effort** → VERIFY with search/reject reason; never invent

## Gap reason format

`[VERIFY: references — no public ClientList match with website work type for this RFP; Thrive Guides blocked (Confirm)]`

## Tests

Confirm block; work-type mismatch; FIN≠win; empty refs→VERIFY no invent; HTA hard facts extract; MCI exclusion.
