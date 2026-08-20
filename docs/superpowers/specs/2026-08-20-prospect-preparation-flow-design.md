# Prospect preparation flow

## Purpose

Make the expanded prospect row read as one preparation flow. Research and Monid enrichment should happen automatically when a qualified prospect is opened. AI preparation remains an intentional user action.

## User flow

1. The user opens a prospect row.
2. The client loads the research brief and starts contact enrichment at the same time.
3. The detail area shows a single preparation state while those requests are pending.
4. When both requests settle, the user can select Generate preparation.
5. The AI receives the research brief plus any company and person data returned by Monid.
6. The user reviews the preparation and can mark the prospect ready for review. The app does not draft, queue, or send outreach.

## Layout

The expanded row has a numbered flow at the top:

1. Research and verification
2. AI preparation
3. Outreach review

Research brief and enrichment details share the first area. Enrichment is a status within that area, not a separate end-of-page card. AI preparation sits beside or below it, depending on viewport width. Outreach review stays last.

## States and failures

- Research brief loading: show that research is loading.
- Enrichment loading: show that verification is in progress.
- Both requests pending: disable Generate preparation and state why.
- Monid finds no match or returns an error: show the research brief, identify enrichment as unavailable, and enable Generate preparation once the request settles.
- Research brief fails: show the error and do not offer AI preparation because there is no base context.
- No Monid configuration: skip enrichment, identify it as unavailable, and allow preparation from the research brief.

## Data flow

The browser keeps the latest enrichment result per lead. When Generate preparation is selected, it sends that result with the existing brief request. The backend adds those fields to the facts passed to `synthesize_brief`.

The facts include the existing score, score reasons, CRM company details, case studies, and activity. When available, they also include company firmographics and contact name, title, role, seniority, phone, employer, and LinkedIn profile. The AI prompt must retain its existing rules: no invented facts, no outreach copy, and explicit warnings for inferred or missing data.

## Scope

The change does not persist enrichment to HubSpot, change scoring, or introduce background jobs. Opening a row starts one brief request and one enrichment request for that browser session. Existing per-lead client state prevents repeats while that row remains open.

## Checks

- Unit tests cover composing AI facts from a brief and enrichment result.
- Frontend tests cover preparation readiness after success, no match, and Monid unavailability.
- Existing lead enrichment and preparation tests continue to pass.
