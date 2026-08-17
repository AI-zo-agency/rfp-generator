# QuickBooks report permission denied (5020)

Date: 2026-08-13
Status: open. Review later. Not a code bug.

## What happened

The 2026-08-13 backfill for realm `1233860570` (production) ingested Query entities for about 3 minutes, then aborted with HTTP 500.

Intuit returned HTTP 400 on a Reports API call:

```
Permission Denied Error
code 5020
element ReportName
Detail: To access this, sign in again or contact an administrator.
```

The OAuth token was valid. Invoice, Bill, Payment, and other Query calls succeeded. The failure is `GET /v3/company/{realm}/reports/{ReportName}`.

## Which report

The connected company 5020s on invalid report IDs such as `ExpensesByVendorSummary`. Other reports (P&L, CustomerIncome, and similar) have been succeeding.

The first backfill run did not log the report name before aborting. A later live check showed `SalesByCustomer` and `ExpensesByVendorSummary` 5020 because those are not Intuit report IDs; the QBO UI titles map to `CustomerSales` and `VendorExpenses`. Wrong IDs also return 5020 with `element=ReportName`. If a correctly named report 5020s, the same skip path applies.

## Why Intuit returns 5020

The connected Intuit app, or the QBO user who authorized it, is not allowed to run that report. Typical causes:

1. The Intuit app OAuth scopes do not include that report.
2. The QBO user who connected the app cannot open that report in QuickBooks (role, custom permissions, or report-pack restriction).
3. The company file does not expose that report for the authorized user.

It is not a bad date range, a broken refresh token, or a schema problem.

## What the sync now does

`_ingest_reports` in `backend/app/financial/qb_sync.py` catches `QuickBooksError` when the message contains `5020` or `Permission Denied`, logs `operation=ingest_report skipped=true reason=permission_denied`, and continues.

Other report errors still fail the run. Entity ingest, remaining reports, company info, panel cache, `cdc_cursor`, and `backfill_completed_at` still run.

## Dashboard impact

`qb_panels_from_db` already degrades missing snapshots: the panel lands in overview `errors` and the rest of the ledger still renders.

If `VendorExpenses` is skipped, the Expenses by vendor panel is empty / error for that year unless the entity fallback fills it. No snapshot row is written for that `(report_name, year, params_hash)`.

`/quickbooks/status.last_error` can still show the old 5020 until a sync finishes successfully.

## How to unblock the report (later)

1. In QBO, sign in as the same user who authorized the app and open Reports → Expenses by Vendor Summary. If that UI is blocked, the API will stay blocked.
2. In the Intuit Developer portal, check the app’s Accounting / reports scopes for this production client.
3. Re-authorize the app with a QBO admin if the connecting user lacks report access.
4. Re-run backfill. An explicit `{"mode":"backfill"}` POST clears completed progress and re-fetches entities plus reports.

If Intuit then returns the report, the snapshot is upserted and the panel fills on the next successful cache write.

## Related files

- `backend/app/financial/qb_sync.py` — skip path
- `backend/app/financial/qb_panels_from_db.py` — missing snapshot → `errors`
- `docs/QUICKBOOKS_INTEGRATION.md` — report list and Railway cron
