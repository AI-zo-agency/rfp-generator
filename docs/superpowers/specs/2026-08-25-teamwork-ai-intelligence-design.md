# Teamwork AI Intelligence drawer

## Purpose

Add an AI Intelligence drawer to the Teamwork dashboard. It turns the existing Teamwork operational data into a short delivery brief, ranked evidence cards, contextual recommendations, and grounded chat. It also begins collecting capacity history for later staffing recommendations.

QuickBooks remains the financial source of truth. Teamwork insights describe delivery, workload, timing, and Teamwork budget exposure.

## User experience

The Teamwork toolbar has an AI Intelligence button matching the QuickBooks trigger. The drawer contains:

- A data-freshness label and a Regenerate action.
- A concise Delivery brief.
- Filterable cards tagged High impact, Risk, Watch, Opportunity, or Action.
- Cards for project health, unassigned overdue work, deadline pressure, late milestones, Teamwork budget exposure, billability, and capacity.
- A link on each card to the applicable Teamwork Projects, Tasks, or Time view.
- Contextual chat that answers only from the same calculated evidence as the brief.

Before adequate history exists, capacity cards state that the staffing trend is still being established. They do not make a hiring recommendation.

## Capacity policy

Each person has 40 available hours per week. A person at 34 or more logged hours is at 85% capacity.

- Watch: a person is at or above 85% in the current week.
- High impact: a person is at or above 85% for three consecutive weekly snapshots.
- Hiring signal: two or more people are at or above 85% for three consecutive weekly snapshots, or total team utilization remains at or above 85% for three consecutive weekly snapshots.

The recommendation names the people or team, the relevant weeks, and the computed utilization. AI does not calculate capacity or infer a staffing need without the stored evidence.

## Architecture and data flow

1. The existing Teamwork sync writes the current operational snapshot.
2. A daily Teamwork capacity snapshot stores each person's logged and billable hours, workload percentage, overdue and due-soon task count, project exposure, and Teamwork budget exposure. Weekly analysis groups these daily records by calendar week.
3. Pure backend derivation produces current signals, historical capacity signals, and the evidence payload.
4. A Teamwork AI endpoint reads the current evidence plus the latest valid stored brief. Regeneration produces a fresh brief and row notes, validates known card IDs and figures, then persists the result.
5. The frontend hook loads the payload. The drawer renders cards and chat from it without duplicating backend calculations.

## Reliability

When Teamwork is disconnected, stale, or partially synced, the drawer shows a freshness warning. It keeps the latest valid brief but does not generate a new one from incomplete data.

The model receives precomputed facts and fixed card identifiers. The response contains a short brief and notes keyed only to those identifiers. Validation drops unknown identifiers and unsupported quantities. The UI still renders deterministic cards if AI generation fails.

## Testing

- Unit tests for current workload thresholds, three-week capacity detection, and hiring-signal conditions.
- Unit tests for snapshots with insufficient history and for partial/stale Teamwork data.
- API tests for evidence construction, response validation, and regeneration fallback.
- Frontend tests for card filtering, badge counts, drawer navigation, and capability-history messaging.

## Scope boundaries

This release does not include Slack notifications, payroll forecasting, or a forecast based on project effort estimates. It uses Teamwork's logged hours against the fixed 40-hour weekly baseline. Any later planning forecast requires planned hours or scoped estimates from Teamwork.
