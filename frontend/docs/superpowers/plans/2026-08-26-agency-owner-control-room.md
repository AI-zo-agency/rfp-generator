# Agency Owner Control Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one Agency control room where an owner can triage delivery, AR, mapping, and unlinked QuickBooks invoices, then resolve links in place.

**Architecture:** Extend the existing agency overview payload with invoice-level reconciliation state, backed by a small app-owned invoice-resolution table. Client-to-QuickBooks mapping remains the source of truth; a distinct invoice-to-Teamwork-project resolution is stored only when an owner links or classifies an invoice. The frontend derives a deterministic action queue from the overview and renders all owner workflows in one surface.

**Tech Stack:** FastAPI + Pydantic, Supabase/Postgres, Next.js 16, React 19, TypeScript, Vitest, existing zö ledger CSS and Lucide icons.

---

## File structure

- `backend/supabase/migrations/20260826_agency_invoice_resolution.sql` — durable invoice reconciliation records.
- `backend/app/financial/client_map_repository.py` — reads and upserts invoice resolutions.
- `backend/app/financial/agency_overview.py` — produces invoice exceptions and action-safe overview data.
- `backend/app/financial/router.py` — validates and exposes in-place invoice resolution.
- `backend/tests/test_client_map_repository.py`, `backend/tests/test_agency_overview.py`, `backend/tests/test_financial_router.py` — backend coverage.
- `frontend/src/financial/types/agency.ts` — Agency API contracts.
- `frontend/src/financial/lib/agency-action-queue.ts` and test — pure priority derivation.
- `frontend/src/financial/components/AgencyResolutionDrawer.tsx` and test — accessible in-place drawer.
- `frontend/src/financial/components/AgencyJobsDemo.tsx` and test — owner-control-room composition.
- `frontend/src/financial/components/ClientMapPanels.tsx` — accepts project context when mapping is opened from triage.
- `frontend/src/financial/components/QuickBooksLedger.css` — narrowly scoped Agency layout and drawer styles.

### Task 1: Persist invoice resolutions

**Files:**

- Create: `backend/supabase/migrations/20260826_agency_invoice_resolution.sql`
- Modify: `backend/app/financial/client_map_repository.py`
- Modify: `backend/tests/test_client_map_repository.py`

- [ ] **Step 1: Write the failing repository tests**

```python
def test_list_invoice_resolutions_filters_realm(monkeypatch):
    q = FakeQuery(data=[{"realm_id": "realm", "invoice_id": "42"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    assert repo.list_invoice_resolutions("realm") == [{"realm_id": "realm", "invoice_id": "42"}]
    assert ("table", "agency_invoice_resolution") in q.calls
    assert ("eq", "realm_id", "realm") in q.calls


def test_upsert_invoice_resolution_uses_realm_and_invoice_conflict(monkeypatch):
    q = FakeQuery(data=[{"realm_id": "realm", "invoice_id": "42", "resolution": "internal"}])
    monkeypatch.setattr(repo, "_get_client", lambda: FakeClient(q))
    row = repo.upsert_invoice_resolution({"realm_id": "realm", "invoice_id": "42", "resolution": "internal"})
    assert row["resolution"] == "internal"
    assert any(call[0] == "upsert" and call[2] == "realm_id,invoice_id" for call in q.calls)
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd backend && pytest tests/test_client_map_repository.py -q`

Expected: FAIL because `list_invoice_resolutions` and `upsert_invoice_resolution` do not exist.

- [ ] **Step 3: Add the migration and repository functions**

```sql
CREATE TABLE IF NOT EXISTS agency_invoice_resolution (
  realm_id TEXT NOT NULL,
  invoice_id TEXT NOT NULL,
  resolution TEXT NOT NULL CHECK (resolution IN ('linked', 'internal')),
  project_id BIGINT,
  client_map_id UUID REFERENCES client_map(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (realm_id, invoice_id),
  CHECK ((resolution = 'linked' AND project_id IS NOT NULL) OR (resolution = 'internal' AND project_id IS NULL))
);
ALTER TABLE agency_invoice_resolution ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agency_invoice_resolution FROM anon, authenticated;
GRANT ALL ON TABLE agency_invoice_resolution TO service_role;
```

Add `_INVOICE_RESOLUTION_TABLE = "agency_invoice_resolution"`. Implement `list_invoice_resolutions(realm_id)` with `.select("*").eq("realm_id", realm_id)`. Implement `upsert_invoice_resolution(payload)` with `updated_at: _now_iso()` and `on_conflict="realm_id,invoice_id"`. Extend the `FakeClient.table` allow-list in the test to include the new table.

- [ ] **Step 4: Run the repository tests**

Run: `cd backend && pytest tests/test_client_map_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/supabase/migrations/20260826_agency_invoice_resolution.sql backend/app/financial/client_map_repository.py backend/tests/test_client_map_repository.py && git commit -m "feat(financial): persist agency invoice resolutions"
```

### Task 2: Add invoice exceptions to the overview and API

**Files:**

- Modify: `backend/app/financial/agency_overview.py`
- Modify: `backend/app/financial/router.py`
- Modify: `backend/tests/test_agency_overview.py`
- Create: `backend/tests/test_financial_router.py`

- [ ] **Step 1: Write the failing overview tests**

```python
def test_unlinked_invoices_skips_deleted_and_resolved_rows():
    invoices = [
        {"qbo_id": "1", "doc_number": "1001", "customer_name": "Acme", "total_amt": 800, "balance": 200, "is_deleted": False},
        {"qbo_id": "2", "doc_number": "1002", "customer_name": "Acme", "total_amt": 500, "balance": 0, "is_deleted": False},
        {"qbo_id": "3", "doc_number": "1003", "customer_name": "Acme", "total_amt": 50, "balance": 0, "is_deleted": True},
    ]
    resolutions = {"1": {"resolution": "internal"}, "2": {"resolution": "linked", "project_id": 77}}
    assert unlinked_invoices(invoices, resolutions=resolutions) == []


def test_unlinked_invoices_sorts_open_ar_before_total_amount():
    invoices = [
        {"qbo_id": "1", "doc_number": "1001", "customer_name": "Acme", "total_amt": 800, "balance": 200, "is_deleted": False},
        {"qbo_id": "2", "doc_number": "1002", "customer_name": "Beta", "total_amt": 1000, "balance": 0, "is_deleted": False},
    ]
    rows = unlinked_invoices(invoices, resolutions={})
    assert [row["invoice_id"] for row in rows] == ["1", "2"]
    assert rows[0]["open_ar"] == 200
```

- [ ] **Step 2: Run the tests to verify the helper is missing**

Run: `cd backend && pytest tests/test_agency_overview.py -q`

Expected: FAIL importing `unlinked_invoices`.

- [ ] **Step 3: Implement the overview helper and response fields**

Implement `unlinked_invoices(invoices, *, resolutions)` in `agency_overview.py`. It must skip deleted invoices and invoice IDs resolved as `linked` or `internal`; return `invoice_id`, `invoice_number`, `customer_id`, `customer_name`, `txn_date`, `due_date`, `total_amt`, and `open_ar`; sort by descending open AR, descending total amount, then invoice ID. In `build_agency_overview`, fetch YTD invoices once, use them for `money_by_customer_id`, read `map_repo.list_invoice_resolutions(realm_id)`, and append:

```python
"unlinked_invoices": unlinked_invoices(
    invoices,
    resolutions={str(row["invoice_id"]): row for row in invoice_resolutions},
)[:40],
"resolution_options": [
    {"project_id": row["project_id"], "project_name": row["project_name"], "company_name": row["company_name"], "client_map_id": row["client_map_id"]}
    for row in jobs
],
```

- [ ] **Step 4: Add the validated resolution endpoint**

In `router.py`, add `InvoiceResolutionUpsert` with `invoice_id: str`, `resolution: Literal["linked", "internal"]`, `project_id: int | None = None`, and `client_map_id: str | None = None`. Add a Pydantic model validator that requires a project for `linked` and forbids one for `internal`. Implement `POST /financials/agency/invoice-resolutions`: require the project to appear in `overview_from_cache()["projects"]`, then pass `realm_id`, the body, and the valid project ID to `upsert_invoice_resolution`; return `404` for an unknown project. Test `422` for a linked payload without `project_id` and `404` for an unknown project.

- [ ] **Step 5: Run backend coverage**

Run: `cd backend && pytest tests/test_agency_overview.py tests/test_client_map_repository.py tests/test_financial_router.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/financial/agency_overview.py backend/app/financial/router.py backend/tests/test_agency_overview.py backend/tests/test_financial_router.py && git commit -m "feat(financial): flag unlinked agency invoices"
```

### Task 3: Define and test owner triage data

**Files:**

- Modify: `frontend/src/financial/types/agency.ts`
- Create: `frontend/src/financial/lib/agency-action-queue.ts`
- Create: `frontend/src/financial/lib/agency-action-queue.test.ts`

- [ ] **Step 1: Write the failing priority test**

```ts
it("orders late delivery before mapping, AR, and invoice reconciliation", () => {
  const actions = buildAgencyActions(overview({
    jobs: [job({ project_id: "late", status: "late" }), job({ project_id: "map", join: "needs mapping" })],
    unlinked_invoices: [invoice({ invoice_id: "inv-1", open_ar: 500 })],
  }));
  expect(actions.map((action) => action.kind)).toEqual(["delivery", "mapping", "receivable", "invoice"]);
});

it("does not create a receivable action for a zero-AR client", () => {
  expect(buildAgencyActions(overview({ jobs: [job({ open_ar: 0 })] })).some((action) => action.kind === "receivable")).toBe(false);
});
```

- [ ] **Step 2: Run it to verify the module is missing**

Run: `cd frontend && npm test -- agency-action-queue.test.ts`

Expected: FAIL resolving `./agency-action-queue`.

- [ ] **Step 3: Implement types and deterministic derivation**

Add `AgencyInvoiceException` and `AgencyResolutionOption` to `types/agency.ts`, then add `unlinked_invoices` and `resolution_options` to `AgencyOverview`. In `agency-action-queue.ts`, export a discriminated `AgencyAction` union containing `id`, `kind`, `priority`, `title`, `detail`, `amount`, plus the relevant `projectId` or `invoiceId`. Use the explicit priority map below; deduplicate receivables by client map/customer, never sum money across jobs, and sort by priority, amount, then title.

```ts
const PRIORITY = { delivery: 0, mapping: 1, receivable: 2, invoice: 3 } as const;

export function buildAgencyActions(data: AgencyOverview): AgencyAction[] {
  // Purely derive actionable records; no fetches or React state.
}
```

- [ ] **Step 4: Run focused frontend tests**

Run: `cd frontend && npm test -- agency-action-queue.test.ts agency-project-groups.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/financial/types/agency.ts frontend/src/financial/lib/agency-action-queue.ts frontend/src/financial/lib/agency-action-queue.test.ts && git commit -m "feat(financial): derive agency owner action queue"
```

### Task 4: Build the direct-resolution drawer

**Files:**

- Create: `frontend/src/financial/components/AgencyResolutionDrawer.tsx`
- Create: `frontend/src/financial/components/AgencyResolutionDrawer.test.tsx`
- Modify: `frontend/src/financial/components/QuickBooksLedger.css`

- [ ] **Step 1: Write the failing drawer test**

Render an invoice action with two project options and assert the dialog is named `Resolve invoice 1001`, contains a project select and `Mark internal revenue` button, and submits the selected project exactly as follows:

```ts
expect(onResolveInvoice).toHaveBeenCalledWith({
  invoice_id: "inv-1",
  resolution: "linked",
  project_id: 42,
  client_map_id: "acme",
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm test -- AgencyResolutionDrawer.test.tsx`

Expected: FAIL resolving `./AgencyResolutionDrawer`.

- [ ] **Step 3: Implement the controlled accessible drawer**

Create `AgencyResolutionDrawer` with `action`, `options`, `open`, `onOpenChange`, `onResolveInvoice`, and `onOpenMapping` props. Use `role="dialog"`, `aria-modal="true"`, a visible close button, focus restoration to the invoking control, disabled controls while saving, and inline failure feedback. For invoice actions, show QuickBooks invoice number/customer/total/open-AR/due-date; require a selected project for linking and separately support internal classification. For mapping actions, show the job/company/join evidence and call the existing Mapping tab via `onOpenMapping(projectId)` rather than duplicating client-map APIs.

Add only `.agency-drawer*` styles: fixed right drawer plus backdrop at desktop, full-width stacked sheet below 720px, and reduced-motion-safe transitions.

- [ ] **Step 4: Run test and lint**

Run: `cd frontend && npm test -- AgencyResolutionDrawer.test.tsx && npm run lint -- src/financial/components/AgencyResolutionDrawer.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/financial/components/AgencyResolutionDrawer.tsx frontend/src/financial/components/AgencyResolutionDrawer.test.tsx frontend/src/financial/components/QuickBooksLedger.css && git commit -m "feat(financial): add agency resolution drawer"
```

### Task 5: Compose the owner control room

**Files:**

- Modify: `frontend/src/financial/components/AgencyJobsDemo.tsx`
- Create: `frontend/src/financial/components/AgencyJobsDemo.test.tsx`
- Modify: `frontend/src/financial/components/ClientMapPanels.tsx`
- Modify: `frontend/src/financial/components/QuickBooksLedger.css`

- [ ] **Step 1: Write a failing page-level order and resolution test**

Mock the overview fetch with one late job, one unmapped job, and one unlinked invoice. Assert `Needs your attention` occurs before `Client portfolio`; click the invoice `Resolve` control and assert the resolution dialog opens. Expand a portfolio row and assert its child job label remains visible.

- [ ] **Step 2: Run the test to confirm the table-first screen fails**

Run: `cd frontend && npm test -- AgencyJobsDemo.test.tsx`

Expected: FAIL because the action queue and invoice drawer are absent.

- [ ] **Step 3: Replace the composition while preserving non-additive money**

Keep the single overview fetch, refresh behavior, `groupJobsByProject`, and `pickMoney` contract. Add a compact snapshot with freshness and the four figures. Render `buildAgencyActions(data)` in a `Panel title="Needs your attention"`; every row has reason, scope, impact, status text, and one named control. Place the drawer at the component root; invoice saves POST to `/api/v1/financials/agency/invoice-resolutions`, show errors inside the drawer, close only after success, then call `load()` to refresh all connected regions.

Rename the full table `Client portfolio` and add `FilterChips` for `all`, `attention`, `late`, `mapping`, and `financial`, filtering only rendered groups. Replace the current orphan-first companion panel with `Unlinked invoices` (invoice number, customer, total, open AR, resolve action), retaining the existing aggregate `Billed, no live Teamwork project` table as the smaller second watchlist.

Thread `onOpenMapping(projectId)` into `ClientMapPanels`, switch to the mapping view, and prefill its job-override project field. The user remains in the Agency tab throughout.

- [ ] **Step 4: Add scoped CSS for hierarchy and responsive behavior**

Use new `.agency-*` rules only: action-row grid that collapses to content/action stacking below 720px, emphasis bands for actual risk, compact freshness row, filter spacing, and a resolved empty-state treatment. Do not alter generic `.qb-table`, `.qb-panel`, or global tokens solely for the Agency screen.

- [ ] **Step 5: Run all focused frontend checks**

Run: `cd frontend && npm test -- AgencyJobsDemo.test.tsx AgencyResolutionDrawer.test.tsx agency-action-queue.test.ts agency-project-groups.test.ts && npm run lint && npm run build`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/financial/components/AgencyJobsDemo.tsx frontend/src/financial/components/AgencyJobsDemo.test.tsx frontend/src/financial/components/ClientMapPanels.tsx frontend/src/financial/components/AgencyResolutionDrawer.tsx frontend/src/financial/components/QuickBooksLedger.css && git commit -m "feat(financial): redesign agency owner control room"
```

### Task 6: Verify the integrated experience

**Files:**

- Modify only reproducibly defective files from Tasks 1–5.

- [ ] **Step 1: Apply the invoice-resolution migration through the project’s normal Supabase workflow**

Verify `agency_invoice_resolution` exists and only `service_role` retains access.

- [ ] **Step 2: Run relevant automated suites**

Run: `cd backend && pytest tests/test_agency_overview.py tests/test_client_map_repository.py tests/test_financial_router.py -q && cd ../frontend && npm test -- agency-action-queue.test.ts agency-project-groups.test.ts AgencyResolutionDrawer.test.tsx AgencyJobsDemo.test.tsx && npm run lint && npm run build`

Expected: PASS.

- [ ] **Step 3: Manually verify real-data desktop and mobile paths**

At approximately 1440px and 390px wide, verify late/mapping/AR/invoice exceptions appear before the portfolio; an invoice can be linked to a displayed Teamwork project or classified internal and then disappears after refresh; failures remain visible with readable feedback; expanded client money stays client-level rather than being summed across child jobs; mapping action lands in the in-tab Mapping workflow with project context.

- [ ] **Step 4: Run the required UI detector once**

Run: `node /Users/princepatel/.codex/plugins/cache/impeccable/impeccable/4.1.1/skills/impeccable/scripts/detect.mjs --json frontend/src/financial/components/AgencyJobsDemo.tsx frontend/src/financial/components/AgencyResolutionDrawer.tsx frontend/src/financial/components/QuickBooksLedger.css`

Expected: record and resolve actionable findings in one bounded fix pass.

## Plan self-review

- **Spec coverage:** snapshot, action queue, portfolio, direct mapping/invoice resolution, AR context, watchlists, loading/error/empty states, responsiveness, accessibility, non-additive money, persistence, and verification all map to Tasks 1–6.
- **Scope:** invoice-level reconciliation requires a durable record, so the plan deliberately does not fake resolution in browser state. AR follow-up remains contextual because no existing contract records outreach or collection.
- **Consistency:** invoice IDs use QuickBooks `qbo_id`; project IDs are Teamwork IDs; optional client-map IDs accompany selected projects.
- **Placeholder scan:** no undefined implementation stages or ambiguous data ownership remain.
