# zö dashboard 2026 — Tab Guide

**How to read this doc**

- Tabs are listed in sheet order (left → right).
- **Hidden** means the tab is not shown in the sheet tab bar but still exists (often prior-year or formula sources).
- Row counts are approximate “last nonempty row” as of the Aug 2026 snapshot — live data will grow.

---



## Quick map by job


| Need                                    | Start here                              |
| --------------------------------------- | --------------------------------------- |
| What should we invoice / forecast cash? | `🧾 QB-Inv`, `☠️ NO`, `🪙 FP-Inv`       |
| Vendor / contractor spend               | `🧾PO's`, `🛒Procurement`, `🛒IMPACT`   |
| RFP pipeline & win rate                 | `RFPs ☑️`, `RFP 📈`, `RFP⌛`             |
| Client health & AMs                     | `🧱 Clients`, `🔤Tags`, `Churn`         |
| Project numbers & margin learning       | `🔢 Project`, `Post Mortem`, `Overages` |
| Media / paid                            | `Ad spend`                              |
| Relationship touches                    | `Client 🎁`                             |
| Portal logins (sensitive)               | `Copy of ADMIN`                         |


---



## Visible tabs



### 1. 🛒 Procurement


|                     |                                                                                                                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                    |
| **~Data rows**      | ~23                                                                                                                                                                                   |
| **Why it’s useful** | Tracks print, swag, and other buy-side requests from quote → order → complete. Ops and AMs see status, vendor cost (IN), client price (OUT), and shipping/invoice notes in one place. |


**Data it contains**


| Column / field                 | Meaning                                                  |
| ------------------------------ | -------------------------------------------------------- |
| AM                             | Account manager who requested                            |
| Date                           | Request / activity date                                  |
| Status                         | e.g. Pricing, Ready for AM, Ordered, Complete, Cancelled |
| Client                         | Client tag / short name                                  |
| Project                        | What is being procured                                   |
| Reference IN                   | Link to request / brief                                  |
| Owned by                       | Usually procurement owner (e.g. Oyetola)                 |
| Expect by                      | Target date                                              |
| IN price total                 | Cost to zö / vendor quote                                |
| OUT price total                | Amount billed (or to bill) to client                     |
| Source                         | Vendor (Printograph, B2sign, etc.)                       |
| Related Invoice / POs          | Cross-links                                              |
| Notes / Tracking / Invoices IN | Tracking numbers, invoice refs                           |


---



### 2. 🪙 FP-Inv


|                     |                                                                                                                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                                               |
| **~Data rows**      | ~231                                                                                                                                                                                                             |
| **Why it’s useful** | Operating invoice queue from Function Point into QuickBooks. Answers: what is ready to send, when, how much, retainer vs project, and whether QB already has it. Primary bridge between PM (FP) and ledger (QB). |


**Data it contains**


| Column / field             | Meaning                          |
| -------------------------- | -------------------------------- |
| Column 1                   | Often create / list date         |
| Client                     | Client code                      |
| Job #                      | FP / project number              |
| Project Name               | Full job title                   |
| AM                         | Account manager                  |
| Invoice Description        | Line description for the invoice |
| FP Inv Link                | Link to FP invoice               |
| Type                       | Retainer / Project (etc.)        |
| ready                      | Boolean — ready to send          |
| Send on                    | Target send date                 |
| Send if                    | Rule (e.g. “On send date”)       |
| Due on                     | Due terms or date                |
| Amount                     | Invoice amount                   |
| QB Status                  | e.g. Invoice sent                |
| Vendor Fees Incl.          | Pass-through vendor fees if any  |
| Ops QA (Initials)          | Ops sign-off                     |
| Billing Period (Start–End) | Period covered                   |
| Exception Logged?          | Y/N                              |
| Notes / Flagged            | Exceptions and flags             |


---



### 3. 🧾 PO's


|                     |                                                                                                                                                                                                          |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                                       |
| **~Data rows**      | ~86                                                                                                                                                                                                      |
| **Why it’s useful** | Purchase-order register for contractors and vendors (Simplifi, freelancers, E2M, print partners). Ties PO amount to client bill amount and QB PO links so cost of delivery can be reconciled to revenue. |


**Data it contains**


| Column / field                     | Meaning                         |
| ---------------------------------- | ------------------------------- |
| On date                            | PO / request date               |
| Client                             | Client code                     |
| Project Description                | What the vendor is doing        |
| AM                                 | Account manager                 |
| Contractor/Vendor                  | Payee                           |
| Function Point                     | FP job id                       |
| FP PO link                         | Link in FP                      |
| PO amt                             | Amount on the PO (cost)         |
| Client bill amt                    | Related client revenue          |
| PO sent                            | Date sent                       |
| QB PO Link                         | QuickBooks PO                   |
| Client Invoice                     | Related client invoice link     |
| Review date / Complete / Review by | Internal QA                     |
| Ref / time Oye / time Sj / Value   | Internal timing / admin columns |


Also includes a vendor note to send invoices to accounting addresses (do not treat as a public distribution list from this doc alone).

---



### 4. 🧾 QB-Inv


|                     |                                                                                                                                                                                                                                                                                               |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                                                                                                                            |
| **~Data rows**      | ~134                                                                                                                                                                                                                                                                                          |
| **Why it’s useful** | **Living invoicing + revenue forecast** by half-month buckets. Header totals roll monthly booked/forecast dollars (~$1.12M YTD vs goal elsewhere). Intended for signed work with IMPACT submitted — not a free-form wish list. Feeds leadership cash/revenue planning and the `☠️ NO` rollup. |


**Data it contains**

- Header row: period totals (Dec half-months through later months of the year).
- Columns: Client, Project, then biweekly amount columns (`Dec 1-15`, `Dec 16-31`, `Jan 1-15`, …).
- Section headers such as **PROJECT WORK - GOVERNMENT** (and related groupings) with line items like Maricopa, Santa Clara, Carbondale, Umatilla, Medford, etc.
- Row totals at far right for many lines.

**Note on the tab:** Header text states it is only for projects that have been signed and IMPACT form submitted — not arbitrary invoices.

---



### 5. Ad spend


|                     |                                                                                                                                                                                         |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                      |
| **~Data rows**      | ~19                                                                                                                                                                                     |
| **Why it’s useful** | Controls paid media: platform, dates, PO/budget caps, who pays (client vs zö card), management fee, and E2M hours. Critical for reconciling media POs, client invoices, and card spend. |


**Data it contains**


| Column / field            | Meaning                                          |
| ------------------------- | ------------------------------------------------ |
| Client                    | Client / campaign owner                          |
| Project Description       | Channel (META, Google, Geofencing/Simplifi, SEO) |
| AM                        | Account manager                                  |
| Run Start / Run End       | Flight dates (or Monthly/Yearly)                 |
| PO                        | Budget / PO amount                               |
| E2M Hours per Month       | Delivery hours                                   |
| Daily / Monthly Spend     | Caps                                             |
| Spend History             | Notes on actuals                                 |
| zö Management Fee         | Agency fee                                       |
| Brief                     | Brief doc link                                   |
| Billing through Client/zö | Who is charged / card path                       |
| Credit Card #             | Last-4 style card ref (sensitive)                |
| Notes                     | Operational nuance                               |


---



### 6. RFPs ☑️


|                     |                                                                                                                                                                                           |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                        |
| **~Data rows**      | ~150                                                                                                                                                                                      |
| **Why it’s useful** | Master **active RFP production board**: who writes/designs, due dates for copy/design/review/submit, value, bonus, portal, and status. Day-to-day tool for keeping proposals on calendar. |


**Data it contains**


| Column / field                                      | Meaning                       |
| --------------------------------------------------- | ----------------------------- |
| Entity name                                         | Prospect / agency issuing RFP |
| ID                                                  | Internal RFP id               |
| Awarding Date / Added on                            | Timeline                      |
| Writer / Designer                                   | Staff assignments             |
| Copy / Design / Review due + ✅ flags                | Milestone tracking            |
| Submit due / By time / submit type                  | Submission logistics          |
| Page limit / Sent in                                | Constraints                   |
| RFP / Proposal / Forms / FP or Trello links         | Artifacts                     |
| Progress Status                                     | e.g. writing/design           |
| 12 mo Value / total mos / Total value / Bonus value | Commercials                   |
| Days to award / RFP Report Status / Where Submitted | Outcome tracking              |


Bonus guidance in header: up to $120k → $500 bonus; over $120k → $1000 (as labeled in-sheet).

---



### 7. RFP 📈


|                     |                                                                                                                                                      |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                   |
| **~Data rows**      | ~57                                                                                                                                                  |
| **Why it’s useful** | Executive **RFP pipeline dashboard** and KPIs (pace to target, win rate, pipeline dollars). Leadership view without scanning every row of `RFPs ☑️`. |


**Data it contains (metrics examples from sheet)**

- In Progress Proposals (vs target 12)
- Current Unawarded / Submitted / Won / Lost counts
- 12-month and total pipeline value for unawarded work
- KPIs: pace to target, proposals needed, closing ratio, decided proposals
- Last-updated timestamp

---



### 8. RFP⌛


|                     |                                                                                                                                                                                               |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                            |
| **~Data rows**      | ~173                                                                                                                                                                                          |
| **Why it’s useful** | **Cost-to-pursue and ROI** for RFPs (and related quota / declined tracking). Shows hours by role, production cost, expected win value — so leadership can see whether bid effort is worth it. |


**Data it contains**


| Column / field                   | Meaning                      |
| -------------------------------- | ---------------------------- |
| RFP name                         | Opportunity                  |
| Source, Fit Analysis             | Who screened + hours         |
| Writer / Designer / Review hours | Effort by role               |
| Print and Delivery, or Notary    | Extra costs                  |
| Cost to produce                  | Fully loaded bid cost        |
| Value / Total Win / ROI          | Commercial outcome           |
| Status                           | Submitted / abandoned / etc. |
| Submitted or Abandoned Date      | Timing                       |
| Summary notes                    | Narrative                    |


Organized in month sections (e.g. Sept, Oct, …).

---



### 9. 🧱 Clients


|                     |                                                                                                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                          |
| **~Data rows**      | ~52                                                                                                                                                                         |
| **Why it’s useful** | **Client book of record** for AMs: status, priority, annual value, contract timing, POCs, living docs, HubSpot, and concern level. Weekly client-health reviews start here. |


**Data it contains**


| Column / field                                             | Meaning                                           |
| ---------------------------------------------------------- | ------------------------------------------------- |
| zö AM                                                      | Account manager(s)                                |
| Client / TAG                                               | Name and short code                               |
| Nature / Priority / Status                                 | High-Medium-Low, Active/Sporadic, letter priority |
| Potential Annual Value                                     | Target book                                       |
| Invoiced thru … / % of Value Remaining                     | Delivery vs plan                                  |
| Contract expiration                                        | Date / seasonality                                |
| Primary client side POC                                    | Contact                                           |
| FP flag                                                    | In Function Point?                                |
| Client Doc / Internal Doc / Living / Status / Budget links | Working docs                                      |
| Daily Rollups / Meetings / Hubspot                         | Operating links                                   |
| Current level of Concern                                   | Risk signal                                       |
| Executive Summary                                          | Short status blurb                                |
| PARTNER agreement / Source                                 | Origin (RFP, SEO, Touches, Referral)              |
| Historical churn / pulse columns                           | Rolling check-in scores                           |


---



### 10. Client 🎁


|                     |                                                                                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Hidden**          | No                                                                                                                                                                       |
| **~Data rows**      | ~26                                                                                                                                                                      |
| **Why it’s useful** | Client **gifting and milestone CRM**: birthdays, work/company anniversaries, mailing addresses, and gift history by quarter/week. Relationship maintenance, not billing. |


**Data it contains**

- TAG, company, recipient, full mailing address
- Birthday / work anniversary / company anniversary dates
- Personal notes
- Gift log columns (Q3/Q4/Q1 and weekly date columns) — e.g. Christmas Gift, Card delivered

---



### 11. w/o


|                     |                                                                                                                                                                            |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                         |
| **~Data rows**      | ~7 (mostly placeholder)                                                                                                                                                    |
| **Why it’s useful** | Intended **work-order** tracker (date, client, description, start/final, how invoiced, Teamwork). Currently sparse / “work in progress” — structure exists for future use. |


**Columns:** Date w/link, Client, Project Description, Start, Final, How invoiced, In Teamwork.

---



### 12. zö🧢🕶️📒


|                     |                                                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                |
| **~Data rows**      | ~6                                                                                                                                |
| **Why it’s useful** | Placeholder sections for **agency swag & collateral inventory** in Bend and “Touches.” Light ops inventory, not financial ledger. |


**Sections labeled:** Swag Inventory in Bend, Collateral Inventory in Bend, Touches, Collateral.

---



### 13. zö🏢


|                     |                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                |
| **~Data rows**      | ~4                                                                                                                |
| **Why it’s useful** | Office ops for **220 NW Oregon Ave, Bend OR** — maintenance and building tenants. Facilities, not client finance. |


---



### 14. library


|                     |                                                                                                                                                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                                              |
| **~Data rows**      | ~21                                                                                                                                                                                                             |
| **Why it’s useful** | Shared **learning / content library** (podcasts, YouTube, articles, books) and a client name list for tagging recommendations (marketing / self-improvement / entertaining). Culture & enablement, not billing. |


---



### 15. Post Mortem


|                     |                                                                                                                                                                                                      |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                                   |
| **~Data rows**      | ~87                                                                                                                                                                                                  |
| **Why it’s useful** | Closed-project **P&L and delivery retrospective**: invoice vs cost vs profit, on-time/on-budget, hours estimate vs actual, what went well / improve / lessons. Feeds scoping and overage prevention. |


**Data it contains**


| Column / field                                            | Meaning                             |
| --------------------------------------------------------- | ----------------------------------- |
| Client / Account Mgr / Project Name / Scope / Type        | Identity                            |
| FP Link                                                   | Job link                            |
| Total Invoice / Total Cost / Total Actuals / Total Profit | Money                               |
| Type                                                      | one time / Monthly / Multi-retainer |
| Start / Final Due / Final Done                            | Schedule                            |
| On time / On Budget                                       | Delivery flags                      |
| Est Hours / Actual Hours                                  | Effort                              |
| What went well / Areas of Improvement / Lessons Learned   | Narrative                           |


---



### 16. 🔤 Tags


|                     |                                                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                |
| **~Data rows**      | ~263                                                                                                                                                              |
| **Why it’s useful** | Canonical **client tag dictionary** (3-letter codes) used across FP, invoices, POs, and other tabs. Prevents naming drift (e.g. ALE = Assisted Living Education). |


**Data it contains**


| Column / field             | Meaning                                               |
| -------------------------- | ----------------------------------------------------- |
| Tag Code                   | Short code                                            |
| Client                     | Full name                                             |
| City / State               | Geography                                             |
| Original A/M / Current A/M | Ownership history                                     |
| Status                     | current / fired / abandoned / rekindle / lost / etc.  |
| Original Source            | How they arrived (RFP, SEO, PPC, Referral, Clutch, …) |
| Highest Value              | Band (e.g. Under/Over $10k)                           |


---



### 17. 🔢 Project


|                     |                                                                                                                                     |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                  |
| **~Data rows**      | ~301                                                                                                                                |
| **Why it’s useful** | Running **project number registry** (e.g. 26147). Source of truth for job IDs referenced in FP-Inv, POs, Overages, and Post Mortem. |


**Data it contains**


| Column / field | Meaning                 |
| -------------- | ----------------------- |
| Start date     | When numbered / started |
| Client         | Tag                     |
| Project#       | Numeric id              |
| Project Type   | Short description       |
| Client name    | Full client name        |


---



### 18. Overages


|                     |                                                                                                                                                                                                                                                |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                                                                             |
| **~Data rows**      | ~103                                                                                                                                                                                                                                           |
| **Why it’s useful** | Formal **write-off / overage log** with dollar impact, hours budget vs used, root cause, and prevention. Header shows cumulative overage dollars (~$106k in snapshot). Critical for leakage and scoping fixes (ties to financial audit goals). |


**Data it contains**


| Column / field             | Meaning             |
| -------------------------- | ------------------- |
| Date Requested             | When logged         |
| A/M / Approved by          | Who owns / approved |
| Client/Project / TAG #     | Job identity        |
| Type of write off          | e.g. Overage hours  |
| $ amount                   | Financial impact    |
| Hours Budgets / Hours used | Effort overrun      |
| What occurred              | Root cause          |
| How can this be averted…   | Prevention          |
| Supporting doc link        | Evidence            |


---



### 19. 🛒 IMPACT


|                     |                                                                                                                                                                                      |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Hidden**          | No                                                                                                                                                                                   |
| **~Data rows**      | ~75                                                                                                                                                                                  |
| **Why it’s useful** | Pre-project **IMPACT / pricing request** workflow: get vendor or internal pricing before a signed project number. Upstream of Procurement and of lines that are allowed on `QB-Inv`. |


**Data it contains**


| Column / field                          | Meaning                                                      |
| --------------------------------------- | ------------------------------------------------------------ |
| On date / Client / Project / AM         | Request identity                                             |
| Quote due / Type                        | Pricing status (Pricing, Ready for AM, Approved to order, …) |
| Reference IN / Deliverable OUT          | Artifacts                                                    |
| Vendor / IN & OUT price totals          | Cost vs sell                                                 |
| Priced by                               | Who priced                                                   |
| Became project #                        | Link once converted to a job                                 |
| Next steps / Ref / time columns / Value | Ops follow-through                                           |


---



### 20. Churn


|                     |                                                                                                                                                    |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                 |
| **~Data rows**      | ~65                                                                                                                                                |
| **Why it’s useful** | Multi-year **client roster + sales + active/inactive status** (2020–2026 columns side by side). Retention and book-of-business analysis over time. |


**Data it contains (repeating pattern per year)**

- Client name
- Sales (YTD or full year)
- Status (Active / Inactive)

Years present: 2026, 2025, 2024, 2023, 2022, 2021, 2020.

---



### 21. Old


|                     |                                                                                                                                                                                          |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                       |
| **~Data rows**      | ~17 (index / links)                                                                                                                                                                      |
| **Why it’s useful** | Index of **legacy dashboards and analyses** (2024 forecast, past overages, tags, 2025 dashboard, etc.). Navigation / archive — sheet warns formulas may be linked; do not casually edit. |


---



### 22. ☠️ NO


|                     |                                                                                                                                                                                               |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                                                                                            |
| **~Data rows**      | 8                                                                                                                                                                                             |
| **Why it’s useful** | Single-row **monthly revenue forecast vs annual goal**. Formula-fed from other tabs (especially QB-Inv). Grey = invoicing complete for month; white = still forecast. Fast leadership glance. |


**Data it contains**


| Field   | Snapshot meaning |
| ------- | ---------------- |
| Jan–Dec | Monthly totals   |
| TOTAL   | ~$1,122,409.52   |
| GOAL    | $1,800,000.00    |


---



### 23. Copy of ADMIN


|                     |                                                                                                                                 |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **Hidden**          | No                                                                                                                              |
| **~Data rows**      | ~30                                                                                                                             |
| **Why it’s useful** | Registry of **RFP / procurement portal accounts** (portal name, state, username, notes, signup history). Needed to submit bids. |


**Data it contains**

- Portal Name, State, User, Password, Notes
- Occasional “check” / date columns for when used

**Security:** This tab stores **plaintext passwords**. Do not export into git, Slack, or public docs. Prefer a password manager and restrict sheet sharing. This documentation intentionally omits credentials.

---



## Hidden tabs



### 24. Forecast - '25 *(hidden)*


|                     |                                                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **~Data rows**      | ~120                                                                                                                             |
| **Why it’s useful** | Prior-year (2025) **project-level revenue forecast** by half-month — historical comparison and formula ancestry for 2026 sheets. |


**Data:** Client, Project, Active flag, Jan–Dec biweekly amount columns; header totals (~$915k projects total in snapshot).

---



### 25. 🛒 Proc-old *(hidden)*


|                     |                                                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------------- |
| **~Data rows**      | ~82                                                                                                             |
| **Why it’s useful** | Older IMPACT/procurement-style request log (superseded by live `🛒Procurement` / `🛒IMPACT`). Kept for history. |


**Data:** Same general shape as IMPACT (date, client, project, AM, quote due, type, vendor, IN/OUT prices, project #, next steps).

---



### 26. Leaderboard '25 *(hidden)*


|                     |                                                                                     |
| ------------------- | ----------------------------------------------------------------------------------- |
| **~Data rows**      | 2                                                                                   |
| **Why it’s useful** | 2025 monthly forecast totals vs goal ($1.2M). Companion rollup to `Forecast - '25`. |


---



### 27. 💰 Pipe *(hidden)*


|                     |                                                                                                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **~Data rows**      | ~46                                                                                                                                                                               |
| **Why it’s useful** | **Weighted pipeline** forecast for 2026: booked signed work by month plus RFP awards weighted into monthly revenue. Strategic sales/finance planning beyond pure booked invoices. |


**Data:**

- Booked signed work row (~$1.12M annual in snapshot)
- Open RFP count
- Per-RFP: estimated award date, name, total value, contract months, weighted monthly amounts

---



### 28. AR-AP *(hidden)*


|                     |                                                                                                                                                                                           |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **~Data rows**      | ~81                                                                                                                                                                                       |
| **Why it’s useful** | Manual **cash / AR calendar** by week-of-month buckets (Dec 2025–Feb 2026 in snapshot): cash on hand plus invoices due by client and week. Cash-flow planning companion to QuickBooks AR. |


**Data:** Cash on Hand; “Invoiced | Due date and amount” grid by client across weekly columns.

---



### 29. 🎁 Gifts *(hidden)*


|                     |                                                                                                                                                |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **~Data rows**      | ~54                                                                                                                                            |
| **Why it’s useful** | Broader / prior **gift CRM** (same shape as `Client 🎁`, with more quarterly columns). Likely superseded or parallel to the visible gifts tab. |


---



### 30. Abigail old *(hidden)*


|                     |                                                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **~Data rows**      | ~7                                                                                                                                                                |
| **Why it’s useful** | Archived **meeting cadence** design (Ops & Creative sync, AM resourcing, client dashboard review, invoice review). Process documentation, not transactional data. |


**Data:** Meeting Name, Purpose, Day of Week, Attendees, Cadence.

---



## How tabs relate (mental model)

```text
Tags + Clients + Project#
        │
        ▼
IMPACT / Procurement ──► PO's ──► costs in QB
        │
        ▼
FP-Inv ──► QB-Inv (forecast grid) ──► ☠️ NO (monthly goal)
        │
        └──► Overages + Post Mortem (margin learning)

RFPs ☑️ ──► RFP 📈 (KPI) + RFP⌛ (cost/ROI) + Pipe (weighted)
Ad spend ──► POs + client invoices (pass-through media)
```

---



## Relation to the app’s QuickBooks integration


| Sheet concept                | Closest QB surface in the app                |
| ---------------------------- | -------------------------------------------- |
| `FP-Inv` / `QB-Inv` invoices | Invoice query + P&L / CustomerIncome         |
| `PO's` vendor costs          | Bills / Purchases / AP aging                 |
| `AR-AP` due calendar         | AR aging panel                               |
| `Overages` / `Post Mortem`   | Not in QB as such — sheet-side leakage intel |
| `Ad spend`                   | Often Purchases + class/department reporting |


The sheet is the **ops workflow + forecast** layer; QuickBooks is the **ledger of record**. Reconciliation work usually means joining FP job #s / client tags from this workbook to QB customers and invoices.

---



## Snapshot caveat

Tab sizes and numbers above reflect a live pull around **August 2026**. Treat counts and dollar totals as illustrative of structure, not as a frozen financial statement.