# Agency owner control room

## Purpose

Turn the Agency tab into the daily operating surface for an agency manager or owner. The page must make the next important decision obvious and let that person investigate and resolve core operational issues without leaving the tab.

## Product truth

The underlying data combines Teamwork projects/jobs, QuickBooks billed revenue and open accounts receivable, and the client-map relationship between them. Money attached to a client must never be summed across that client’s jobs.

## Primary user and scene

An owner opens this tab at the start of the day to answer four questions:

1. What needs my attention today?
2. Which client work is late, under-mapped, or financially exposed?
3. Can I understand the supporting project and accounting facts immediately?
4. Can I resolve the issue here, without switching to another part of the financial workspace?

## Information architecture

### 1. Operating snapshot

At the top, a compact snapshot presents booked YTD, open AR, live jobs, and mapping coverage. It also records data freshness and the refresh affordance. The figures support the work; they do not compete with the action queue for attention.

### 2. Owner action queue

The first major region is an impact-ordered queue of actionable exceptions:

- Late projects and overdue tasks
- Unmapped or ambiguous Teamwork-to-QuickBooks relationships
- Open AR that needs a follow-up
- Billed QuickBooks customers without a live Teamwork project

Each item explains its trigger, identifies the client/project, shows the relevant financial or delivery impact, and has one primary in-place action. Severity and workload indicators make the queue scannable without turning it into a color-heavy alert wall.

### 3. Client portfolio

The full project portfolio remains on the page after the queue. Rows are client-led rather than raw job-led, and show health, live job count, hours this month, billed YTD, open AR, and mapping status. Filters allow the owner to focus on action state, delivery health, or financial exposure. Expanding a client reveals its individual Teamwork jobs and their supporting data, while client-level QuickBooks figures remain clearly non-additive.

### 4. Revenue watchlist

The less urgent "billed, no live Teamwork project" set becomes a compact revenue watchlist. It remains visible in the same control room but does not visually outrank current delivery or direct mapping work.

## In-place resolution

Selecting an actionable mapping issue opens a focused side panel over the control room. It shows the Teamwork project/company, the current relationship evidence, suggested QuickBooks customer matches, and a searchable customer picker. The owner can confirm a match, set an override, or mark internal work, then save. On success, the queue, snapshot coverage, and relevant portfolio row update without a navigation change.

Selecting an AR issue opens an in-place detail panel with the client’s open amount and relevant context. This surface will support recording/reviewing follow-up within the tab; it must not imply that an invoice was collected unless the underlying system provides that capability.

## Interaction and states

- Refresh keeps the current filters and expanded portfolio rows when possible.
- Queue filters and portfolio filters are independent but visible together.
- Loading uses the existing ledger skeleton language.
- Empty states affirm the resolved condition (for example, all live work mapped) and retain access to the portfolio.
- Failed retrieval shows a clear retry state without masking cached data.
- Mapping saves show progress, success feedback, and actionable error copy; no silent state changes.
- Desktop uses a right-side resolution panel. On smaller screens, the same panel becomes a full-width stacked sheet.

## Visual direction

Preserve the established zö operational palette and restrained editorial tone. Replace the current equal-weight KPI-and-table composition with a clear attention gradient: neutral operating snapshot, decisive action queue, then denser portfolio evidence. Teal expresses confirmed/healthy state; amber and red are reserved for real attention and risk. Use strong spacing, table alignment, and compact status treatments rather than decoration.

## Accessibility and quality

- All action controls are keyboard reachable and named.
- Expandable rows preserve Enter/Space behavior and expose their state.
- Status is conveyed in text and iconography, not color alone.
- Numeric columns use tabular figures and retain their existing monetary formatting.
- At narrow widths, priority actions remain above dense data and tables scroll horizontally only where necessary.

## Verification

Implement unit coverage for any new prioritization/grouping helpers, exercise loading/error/empty states, and verify the page at desktop and mobile viewport sizes. Confirm that a client’s billed and AR totals are never summed across repeated jobs. Run the project’s relevant lint/test/build commands and the UI detector against the changed Agency surface.
