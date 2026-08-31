# Agency weekly AI Intelligence — v2

## Purpose

v1 delivers a **weekly** Agency AI drawer: prior-week brief, current-week live cards, carryover from last week, and `weeks_open` aging on stable queue item IDs.

v2 makes that brief **historical and operational** — trends over time, accountability by owner, cross-portal drill-down, and persisted follow-up — without turning Agency into a daily brief or duplicating QuickBooks / Teamwork intelligence.

Relationship to sibling specs:

- [Agency owner control room (2026-08-26)](./2026-08-26-agency-owner-control-room-design.md) — the tab UI and queue
- v1 (implementation plan) — weekly cadence, carryover, drawer + chat

---

## v2 capabilities

### Week picker and archive

- Browse past weekly briefs: week of 25 Aug, 18 Aug, 11 Aug, …
- Each week: stored `brief`, `evidence` snapshot, carryover / resolved / new lists at generation time
- Compare **any two selected weeks** (not only prior vs current)

### Trend strip (4–8 weeks)

Sparklines or compact table, Python-computed, LLM narrates only:

- Priority queue item count
- Carryover count
- Join health (`join_mapped / join_total`)
- Unlinked invoice count and open AR
- Orphan billed total (top-N customers sum)

### Stuck leaderboard

- Single card: items open longest across delivery, mapping, receivable, invoice, orphan kinds
- Sort by `weeks_open`, then amount
- Headline pattern: `Still open since week of 4 August`

### AM / account-owner grouping

- Group carryover and aging by `current_am` from client map when present
- Example: `Kim: 4 items · 2 open 3+ weeks`
- **Never fabricate** AM names — only from client map KB

### Persisted receivable follow-ups

- Replace frontend-only follow-up notes in resolution drawer
- Store: client/key, note, recorded_at, recorded_by (if auth available)
- Weekly brief: `Follow-up logged 26 Aug; still open` vs `No follow-up recorded`
- Does **not** imply payment collected

### Cross-portal enrichment

One-hop context, not duplicate briefs:

| Agency item | Enrichment | goTo |
|-------------|------------|------|
| Delivery carryover | Overdue task count on project (Teamwork cache) | Teamwork Projects |
| Receivable carryover | Oldest overdue invoice age (QB mirror, if mapped) | QuickBooks Open |

### Mapping and invoice velocity

- **Confirmed this week:** jobs/clients moved to confirmed join
- **Invoices linked / marked internal** vs still on carryover list
- **Regressed mapping** (suggested → needs mapping) flagged as data-quality watch

### Orphan lifecycle

- New orphan this week vs carried orphan
- `Billing without live project for N weeks` per customer

### Monday digest (optional)

- Email or Slack when: carryover ≥ threshold, any item ≥ 4 weeks, or join health drops WoW
- One paragraph + deep link to Financial Insights → Agency → AI drawer
- Uses same weekly row; no separate daily noise

### Chat upgrades

- **Compare last 4 weeks** — load last 4 `evidence` blobs, Python summarizes deltas, LLM narrates
- **What did we commit to last week?** — requires persisted follow-ups
- Persist chat threads (`agency_chat_threads`) so Monday standup continues prior thread

### UI polish

- Queue tab filters: `Carried from prior week`, `Open 3+ weeks`
- Row badges: `4 wks`, `Carryover`
- Empty state: `Week of X–Y generating Monday 6:00 AM PT`
- Copy weekly brief as markdown for partner email

---

## Suggested v2 build order

1. Week picker + past brief archive (highest value once v1 is live)
2. 4-week trend strip
3. Stuck leaderboard
4. Persisted receivable follow-ups
5. AM grouping
6. Cross-portal goTo enrichment
7. Mapping / invoice velocity metrics
8. Monday digest (optional, after content is trusted)

---

## Explicitly out of scope (v2 and later)

- **Daily** Agency AI briefs (Agency stays weekly-only)
- Predictive forecasts (`join health will reach 100% by …`)
- Auto-resolve mapping or auto-link invoices
- Combined QuickBooks + Teamwork + Agency mega-brief
- Invented team members, certifications, carriers, or payment-collected claims

---

## Data dependencies

| Feature | Requires |
|---------|----------|
| Trends | ≥ 2 weekly `ai_insights` rows (`source=agency`) |
| Stuck leaderboard | v1 `weeks_open` chain in evidence |
| AM grouping | client map `current_am` populated |
| Follow-up persistence | New table or extension to agency resolution storage |
| TW/QB enrichment | Live TW cache + QB mirror on generate/read |

---

## Success criteria

An owner can answer without leaving Agency:

1. How has carryover trended over the last month?
2. What has been stuck the longest, and who owns it?
3. What did we clear vs leave open last week?
4. Did we log follow-up on aging receivables?
