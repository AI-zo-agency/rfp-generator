# Go/No-Go — Remove AI Fit Score + Lean No-Go Reasons

**Date:** 2026-07-22  
**Status:** Approved for implementation (Approach 1)

## Problem

Overall Go Score (matrix average, e.g. 2.8) and AI Fit Score (e.g. 4/5) measured different things and confused users. Fit looked like a green light while the matrix said lean No-Go.

## Decisions

- Remove **AI Fit Score** from RFP detail / Go/No-Go panel / Stage 1 report text
- Keep **Worth It Score** + **Overall Go Score** (matrix average)
- Keep `fitScore` in API/DB for compatibility (internal / fallback only)
- When **Overall &lt; 3**, show bold **“Why this is leaning No-Go”** immediately under the decision matrix (low matrix rows + critical gaps)
- Tighten analyst prompt: evidence-calibrated scoring; do not auto-`no_go` every weak score; prefer `review` when gaps are fixable
- Run all planned KB queries and round-robin merge hits so later queries are not starved; list searches in KB context

## Out of scope

- Dropping `fit_score` column / schema migration
- Changing analytics “avg fit” tile (can follow later)
