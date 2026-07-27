# Optional `[VERIFY]` Scrub — Design

**Date:** 2026-07-27  
**Status:** Approved

## Problem

Drafts leave `[VERIFY: …]` placeholders (e.g. unnamed subcontractors) even when the RFP does not require that specific fact. Tags should only remain when the RFP still needs the fact and KB could not fill it.

## Rule

- RFP does **not** require the fact → remove the tag and reframe prose (never invent names/contacts).
- RFP **does** require it and still unknown → keep a short `[VERIFY: …]`.

## Architecture

Shared scrubber used by:

1. **Phase 4 finalize** — after KB fill attempts, scrub remaining optional VERIFYs on affected sections.
2. **Section chat** — when user asks to remove/clean/strip VERIFY tags: whole-section reframe + RFP scan via the same scrubber.

## Module

`backend/app/services/proposal_verify_optional_scrub.py`

- Intent detect for chat (“remove VERIFY tags…”)
- LLM rewrite with RFP excerpt + section body
- Report: tags before/after, kept-required count

## Non-goals

- Do not invent facts to fill tags.
- Do not soft-prose required gaps into fake certainty.
- Do not strip legal/attestation VERIFYs that are RFP-required.
