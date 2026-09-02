"use client";

/**
 * The QuickBooks ledger.
 *
 * Reading order is the design: the first screen states the position, then what
 * needs a decision, then the one trend worth a chart. Everything else is a tab
 * away — present, but not competing for the same glance.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { RefreshCw, Sparkles } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AnimatedNumber } from "./AnimatedNumber";
import { AiIntelligenceDrawer } from "./AiIntelligenceDrawer";
import { AgingBar, DataTable, Empty, Figure, Note, Panel, compact, usd } from "./qb-ui";
import { useQbChat } from "../lib/use-qb-chat";
import { useQbInsights } from "../lib/use-qb-insights";
import type { QuickBooksOverview } from "../types/quickbooks";
import "./QuickBooksLedger.css";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

const VIEWS = [
  { id: "today", label: "Position" },
  { id: "open", label: "Open" },
  { id: "revenue", label: "Revenue" },
  { id: "clients", label: "Clients" },
  { id: "costs", label: "Costs" },
] as const;

/* ── chart chrome ──────────────────────────────────────────────────────── */

const AXIS = {
  tickLine: false,
  axisLine: false,
  tick: { fill: "var(--zo-text-muted)", fontSize: 11 },
} as const;

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string; dataKey?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="qb-charttip">
      <p className="qb-charttip-title">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="qb-charttip-row">
          <span className="qb-swatch" style={{ background: p.color }} aria-hidden />
          <span>{p.name}</span>
          <strong>{usd(p.value ?? 0)}</strong>
        </p>
      ))}
    </div>
  );
}

/** Months after the last booked one are noise, not zeroes worth plotting. */
function trimTrailing<T>(rows: T[], hasValue: (row: T) => boolean) {
  let last = -1;
  rows.forEach((row, i) => {
    if (hasValue(row)) last = i;
  });
  return last >= 0 ? rows.slice(0, last + 1) : rows;
}

/* ── position ──────────────────────────────────────────────────────────── */

/** Rounding to whole points: a tenth of a point of margin is below the
 *  precision an estimated cost can carry, and printing it implies otherwise. */
const _POINTS_WORTH_SAYING = 0.5;

/**
 * Margin reads high while a month's bills are still arriving, so the caveat
 * rides on the figure itself. Without it the strip states 53% all quarter and
 * the reader has no way to know 48% is the truer number.
 */
function marginSub(
  pct: number | null | undefined,
  completeness: QuickBooksOverview["cost_completeness"],
) {
  if (pct == null) return undefined;
  const base = `${pct}% of booked`;
  const adjusted = completeness?.adjusted_gross_margin_pct;
  const over = completeness?.overstated_points;
  if (adjusted == null || over == null || over < _POINTS_WORTH_SAYING) return base;
  return `${base} · ~${Math.round(adjusted)}% once late costs land`;
}

function MoneyLine({
  data,
  net,
}: {
  data: QuickBooksOverview;
  net: number;
}) {
  const money = (v: number) => (
    <AnimatedNumber
      value={v}
      prefix={v < 0 ? "-$" : "$"}
      format={(n) => Math.round(Math.abs(n)).toLocaleString("en-US")}
    />
  );
  const cash = data.liquidity;
  const trend = data.monthly_trend;
  const pl = data.pl_summary;

  return (
    <div className="qb-moneyline">
      {cash ? (
        <Figure
          label="Cash on hand"
          size="lg"
          metric="cash"
          value={money(cash.cash)}
          sub={
            cash.net_cash_change != null
              ? `${cash.net_cash_change >= 0 ? "+" : "−"}${compact(Math.abs(cash.net_cash_change))} this year`
              : undefined
          }
        />
      ) : null}
      <Figure
        label="Owed to zö"
        size="lg"
        metric="ar"
        value={money(data.ar?.total ?? 0)}
        tone={data.ar?.overdue_total ? "out" : undefined}
        sub={
          data.ar
            ? data.ar.overdue_total > 0
              ? `${compact(data.ar.overdue_total)} overdue`
              : `${data.ar.invoice_count} open invoices`
            : undefined
        }
      />
      <Figure
        label="zö owes"
        size="lg"
        metric="ap"
        value={money(data.ap?.total ?? 0)}
        sub={data.ap ? `${data.ap.bill_count} open bills` : undefined}
      />
      <Figure
        label="Net position"
        size="lg"
        metric="net"
        value={money(net)}
        tone={net < 0 ? "warn" : undefined}
        sub={net >= 0 ? "Receivables cover payables" : "Payables exceed receivables"}
      />
      <Figure
        label={`Booked ${data.year}`}
        size="lg"
        metric="booked"
        value={money(trend?.total ?? 0)}
        sub={trend?.last_booked_month ? `Through ${trend.last_booked_month}` : undefined}
      />
      {typeof pl?.gross_profit === "number" ? (
        <Figure
          label="Gross margin"
          size="lg"
          metric="margin"
          value={money(pl.gross_profit)}
          sub={marginSub(pl.gross_margin_pct, data.cost_completeness)}
        />
      ) : null}
      {typeof pl?.net_income === "number" ? (
        <Figure
          label="Net income"
          size="lg"
          metric="income"
          value={money(pl.net_income)}
          tone={pl.net_income < 0 ? "warn" : undefined}
          sub="What the books closed to"
        />
      ) : null}
    </div>
  );
}

function CashChart({ bvc }: { bvc: NonNullable<QuickBooksOverview["billing_vs_cash"]> }) {
  const rows = useMemo(
    () => trimTrailing(bvc.by_month, (r) => r.invoiced > 0 || r.collected > 0),
    [bvc.by_month],
  );

  return (
    <Panel
      title="Billed against collected"
      meta={`${Math.round(bvc.collection_rate_pct)}% collected · ${compact(bvc.open_ar)} still open`}
    >
      <div className="qb-legend">
        <span>
          <span className="qb-swatch" style={{ background: "var(--zo-orange)" }} aria-hidden />
          Invoiced
        </span>
        <span>
          <span className="qb-swatch" style={{ background: "var(--zo-teal)" }} aria-hidden />
          Collected
        </span>
      </div>
      <ResponsiveContainer width="100%" height={230}>
        <BarChart
          data={rows}
          margin={{ top: 4, right: 4, bottom: 0, left: -12 }}
          barGap={3}
          barCategoryGap="32%"
        >
          <CartesianGrid vertical={false} stroke="var(--zo-border)" />
          <XAxis dataKey="month" {...AXIS} />
          <YAxis {...AXIS} width={54} tickFormatter={(v: number) => compact(v)} />
          <RTooltip
            cursor={{ fill: "var(--zo-surface)" }}
            content={<ChartTooltip />}
          />
          <Bar dataKey="invoiced" name="Invoiced" fill="var(--zo-orange)" radius={[3, 3, 0, 0]} maxBarSize={26} isAnimationActive={false} />
          <Bar dataKey="collected" name="Collected" fill="var(--zo-teal)" radius={[3, 3, 0, 0]} maxBarSize={26} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </Panel>
  );
}

/* ── clients ───────────────────────────────────────────────────────────── */

interface ClientRow {
  client: string;
  income: number;
  cost: number;
  net: number;
  margin: number | null;
  collected: number;
  avgDays: number | null;
  open: number;
}

/**
 * Profitability, sales, collections, payment lag and open balance used to be
 * four separate ranked lists that never lined up. One row per client instead.
 */
function buildClientRows(data: QuickBooksOverview): ClientRow[] {
  const rows = new Map<string, ClientRow>();
  const at = (name: string) => {
    let row = rows.get(name);
    if (!row) {
      row = { client: name, income: 0, cost: 0, net: 0, margin: null, collected: 0, avgDays: null, open: 0 };
      rows.set(name, row);
    }
    return row;
  };

  data.client_profitability?.clients.forEach((c) => {
    const row = at(c.client);
    row.income = c.income;
    row.cost = c.expense;
    row.net = c.net;
    row.margin = c.margin_pct;
  });
  data.sales_by_customer?.clients.forEach((c) => {
    const row = at(c.client);
    if (!row.income) row.income = c.amount;
  });
  data.cash_collections?.top_payers.forEach((p) => {
    at(p.customer).collected = p.amount;
  });
  data.dso?.slowest_clients.forEach((c) => {
    at(c.client).avgDays = c.avg_days;
  });
  data.ar?.clients.forEach((c) => {
    at(c.client).open = c.amount;
  });

  return [...rows.values()];
}

const money = (v: number) => (v ? compact(v) : "—");

const CLIENT_COLUMNS: ColumnDef<ClientRow, unknown>[] = [
  {
    accessorKey: "client",
    header: "Client",
    cell: (ctx) => <span className="qb-name">{ctx.getValue<string>()}</span>,
  },
  { accessorKey: "income", header: "Income", meta: { numeric: true }, cell: (c) => money(c.getValue<number>()) },
  { accessorKey: "cost", header: "Cost", meta: { numeric: true }, cell: (c) => money(c.getValue<number>()) },
  { accessorKey: "net", header: "Net", meta: { numeric: true }, cell: (c) => money(c.getValue<number>()) },
  {
    accessorKey: "collected",
    header: "Collected",
    meta: { numeric: true },
    cell: (c) => money(c.getValue<number>()),
  },
  {
    accessorKey: "open",
    header: "Open",
    meta: { numeric: true },
    cell: (c) => {
      const v = c.getValue<number>();
      return v ? <span className="qb-tone-out">{compact(v)}</span> : "—";
    },
  },
  {
    accessorKey: "avgDays",
    header: "Pays in",
    meta: { numeric: true },
    cell: (c) => {
      const v = c.getValue<number | null>();
      return v == null ? "—" : `${Math.round(v)}d`;
    },
  },
];

/* ── container ─────────────────────────────────────────────────────────── */

function LedgerSkeleton() {
  return (
    <div className="qb-skel" aria-busy="true" aria-live="polite" aria-label="Reading the ledger">
      <div className="qb-skel-block" style={{ height: 88 }} />
      <div className="qb-two">
        <div className="qb-skel-block" style={{ height: 280 }} />
        <div className="qb-skel-block" style={{ height: 280 }} />
      </div>
    </div>
  );
}

function isAbortError(err: unknown) {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

export function QuickBooksPanels() {
  const currentYear = new Date().getFullYear();
  const years = [currentYear, currentYear - 1, currentYear - 2];
  const [year, setYear] = useState(currentYear);
  const [view, setView] = useState<string>("today");
  const [data, setData] = useState<QuickBooksOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async (y: number) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    // Drop the previous year immediately so stale totals cannot linger
    // while the overview request is in flight.
    setData(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/overview?year=${y}`,
        { signal: ac.signal },
      );
      if (!res.ok) throw new Error(`QuickBooks returned ${res.status}`);
      const payload = (await res.json()) as QuickBooksOverview;
      if (ac.signal.aborted) return;
      setData(payload);
    } catch (err) {
      if (isAbortError(err) || ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Could not reach QuickBooks");
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(year);
    return () => abortRef.current?.abort();
  }, [load, year]);

  const net = (data?.ar?.total ?? 0) - (data?.ap?.total ?? 0);
  const signals = useMemo(() => data?.signals ?? [], [data]);
  const insights = useQbInsights(signals);
  const chat = useQbChat();
  const [aiOpen, setAiOpen] = useState(false);
  const clientRows = useMemo(() => (data ? buildClientRows(data) : []), [data]);
  const trend = data?.monthly_trend;
  const rc = data?.revenue_by_class;
  const am = data?.by_account_manager;
  // The matrix is built from revenue_by_class, so its own coverage is the one
  // that describes what's on screen. class_coverage is the fallback.
  const coverage = rc?.coverage_pct ?? data?.class_coverage?.coverage_pct;
  const managers = am?.managers.filter((m) => !m.is_overhead && m.income > 0) ?? [];
  const managerMax = Math.max(...managers.map((m) => m.income), 1);
  const bookedRows = trend ? trimTrailing(trend.months, (m) => m.amount > 0) : [];
  const syncFailed = !loading && data?.sync_status === "failed";
  let syncLabel = "Synced";
  if (loading) syncLabel = `Reading ${year}…`;
  else if (syncFailed) syncLabel = "Sync failed";

  return (
    <TooltipProvider delayDuration={120}>
      <div className="qb-ledger" aria-busy={loading || undefined}>
        <div className="qb-toolbar">
          <p className="qb-sync" data-failed={syncFailed ? "true" : undefined}>
            <span className="qb-sync-dot" data-busy={loading ? "true" : undefined} aria-hidden />
            {syncLabel}
            {!loading && data?.synced_at ? (
              <span className="qb-sync-meta">{new Date(data.synced_at).toLocaleString()}</span>
            ) : null}
            {!loading && data?.company ? (
              <span className="qb-sync-meta">{data.company.legal_name}</span>
            ) : null}
            {!loading && data?.activity ? (
              <span className="qb-sync-meta">{data.activity.total} ledger changes</span>
            ) : null}
          </p>
          <div className="qb-toolbar-actions">
            <button
              type="button"
              className="qb-ai-trigger"
              onClick={() => setAiOpen(true)}
              aria-haspopup="dialog"
              aria-expanded={aiOpen}
            >
              <Sparkles size={14} strokeWidth={2.25} aria-hidden />
              AI Intelligence
              {insights.highImpact ? (
                <span className="qb-ai-trigger-count">{insights.highImpact}</span>
              ) : null}
            </button>
            <ToggleGroup
              type="single"
              value={String(year)}
              onValueChange={(v) => v && setYear(Number(v))}
              className="qb-years"
              aria-label="Fiscal year"
              aria-busy={loading || undefined}
            >
              {years.map((y) => (
                <ToggleGroupItem key={y} value={String(y)} aria-label={String(y)}>
                  {y}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </div>

        <Tabs value={view} onValueChange={setView} className="qb-tabs">
          <TabsList className="qb-tablist">
            {VIEWS.map((v) => (
              <TabsTrigger key={v.id} value={v.id}>
                {v.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {loading ? <LedgerSkeleton /> : null}
          {!loading && (error || !data) ? (
            <div className="qb-error">
              <p>{error ?? "No QuickBooks data"}</p>
              <button type="button" onClick={() => void load(year)} className="qb-retry">
                <RefreshCw size={13} strokeWidth={2.25} aria-hidden /> Try again
              </button>
            </div>
          ) : null}
          {!loading && data ? (
            <>
          {/* ── position ── */}
          <TabsContent value="today" className="qb-view">
            <MoneyLine data={data} net={net} />
            {data.billing_vs_cash ? (
              <CashChart bvc={data.billing_vs_cash} />
            ) : (
              <Panel title="Billed against collected">
                <Empty>Billing and collection history is unavailable for {data.year}.</Empty>
              </Panel>
            )}
          </TabsContent>

          {/* ── open ── */}
          <TabsContent value="open" className="qb-view">
            <div className="qb-two">
              <Panel
                title="Who owes zö"
                meta={data.ar ? `${usd(data.ar.total)} across ${data.ar.invoice_count} invoices` : undefined}
              >
                {data.ar ? (
                  <>
                    <AgingBar buckets={data.ar.buckets} />
                    <DataTable
                      data={data.ar.clients}
                      pageSize={8}
                      initialSort="amount"
                      empty="No open receivables."
                      columns={[
                        {
                          accessorKey: "client",
                          header: "Client",
                          cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
                        },
                        {
                          accessorKey: "oldest_days",
                          header: "Oldest",
                          meta: { numeric: true },
                          cell: (c) => {
                            const d = c.getValue<number>();
                            return <span className={d > 60 ? "qb-tone-bad" : undefined}>{d}d</span>;
                          },
                        },
                        {
                          accessorKey: "invoices",
                          header: "Invoices",
                          meta: { numeric: true },
                        },
                        {
                          accessorKey: "amount",
                          header: "Open",
                          meta: { numeric: true },
                          cell: (c) => usd(c.getValue<number>()),
                        },
                      ]}
                    />
                  </>
                ) : (
                  <Empty>No open receivables.</Empty>
                )}
              </Panel>

              <Panel
                title="What zö owes"
                meta={data.ap ? `${usd(data.ap.total)} across ${data.ap.bill_count} bills` : undefined}
              >
                {data.ap ? (
                  <>
                    <AgingBar buckets={data.ap.buckets} />
                    <DataTable
                      data={data.ap.vendors}
                      pageSize={8}
                      initialSort="amount"
                      empty="No open payables."
                      columns={[
                        {
                          accessorKey: "vendor",
                          header: "Vendor",
                          cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
                        },
                        {
                          accessorKey: "amount",
                          header: "Open",
                          meta: { numeric: true },
                          cell: (c) => usd(c.getValue<number>()),
                        },
                      ]}
                    />
                  </>
                ) : (
                  <Empty>No open payables.</Empty>
                )}
              </Panel>
            </div>
          </TabsContent>

          {/* ── revenue ── */}
          <TabsContent value="revenue" className="qb-view">
            <Panel
              title={`Booked income, ${data.year}`}
              meta={trend ? usd(trend.total) : undefined}
            >
              {bookedRows.length ? (
                <ResponsiveContainer width="100%" height={210}>
                  <BarChart data={bookedRows} margin={{ top: 4, right: 4, bottom: 0, left: -12 }}>
                    <CartesianGrid vertical={false} stroke="var(--zo-border)" />
                    <XAxis dataKey="month" {...AXIS} />
                    <YAxis {...AXIS} width={54} tickFormatter={(v: number) => compact(v)} />
                    <RTooltip cursor={{ fill: "var(--zo-surface)" }} content={<ChartTooltip />} />
                    <Bar
                      dataKey="amount"
                      name="Booked"
                      fill="var(--zo-teal)"
                      radius={[3, 3, 0, 0]}
                      maxBarSize={38}
                      isAnimationActive={false}
                    />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <Empty>No income booked in {data.year} yet.</Empty>
              )}
              {trend?.last_booked_month ? (
                <Note>
                  {trend.last_booked_month} is the most recent booked month and may still be
                  filling in.
                </Note>
              ) : null}
            </Panel>

            <div className="qb-two">
              <Panel
                title="By segment"
                meta={coverage != null ? `${Math.round(coverage)}% classified` : undefined}
              >
                {rc?.matrix?.length ? (
                  <SegmentMatrix rc={rc} />
                ) : (
                  <Empty>No segment split recorded for {data.year}.</Empty>
                )}
              </Panel>

              <Panel
                title="By account manager"
                meta={managers.length ? `${managers.length} managers` : undefined}
              >
                {managers.length ? (
                  <ul className="qb-bars">
                    {managers.map((m) => (
                      <li key={m.manager}>
                        <span className="qb-name">{m.manager}</span>
                        <span className="qb-bar-track" aria-hidden>
                          <span style={{ width: `${(m.income / managerMax) * 100}%` }} />
                        </span>
                        <span className="qb-num">{compact(m.income)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>Income isn&apos;t split by account manager.</Empty>
                )}
              </Panel>
            </div>
          </TabsContent>

          {/* ── clients ── */}
          <TabsContent value="clients" className="qb-view">
            <Panel
              title="Every client, one row"
              meta={`${clientRows.length} clients`}
              action={
                data.client_profitability ? (
                  <span className="qb-caveat">
                    Only {compact(data.client_profitability.attributed_expense)} of cost is
                    tagged to a client, so margins read high
                  </span>
                ) : null
              }
            >
              <DataTable
                data={clientRows}
                columns={CLIENT_COLUMNS}
                initialSort="income"
                pageSize={12}
                empty="No client activity recorded."
              />
            </Panel>

            <div className="qb-two">
              <Panel
                title="Credits & adjustments"
                meta={data.credit_memos?.count ? `${data.credit_memos.count} memos` : undefined}
              >
                {data.credit_memos?.count ? (
                  <>
                    <Figure label="Issued this year" value={usd(data.credit_memos.total)} />
                    <DataTable
                      data={data.credit_memos.clients}
                      pageSize={5}
                      initialSort="amount"
                      columns={[
                        {
                          accessorKey: "client",
                          header: "Client",
                          cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
                        },
                        {
                          accessorKey: "amount",
                          header: "Credited",
                          meta: { numeric: true },
                          cell: (c) => usd(c.getValue<number>()),
                        },
                      ]}
                    />
                  </>
                ) : (
                  <Empty>No credit memos issued in {data.year}.</Empty>
                )}
              </Panel>

              <Panel title="Collection speed">
                {data.dso?.dso_days != null ? (
                  <>
                    <Figure
                      label="Average time to get paid"
                      size="lg"
                      value={
                        <>
                          {data.dso.dso_days}
                          <span className="qb-unit">days</span>
                        </>
                      }
                      sub={`Across ${data.dso.sample_size.toLocaleString()} invoice payments`}
                    />
                    {data.customers ? (
                      <Note>
                        {data.customers.count} active customers in QuickBooks, keyed and ready
                        to join against Teamwork.
                      </Note>
                    ) : null}
                  </>
                ) : (
                  <Empty>
                    Too few payments are linked back to invoices to measure this.
                  </Empty>
                )}
              </Panel>
            </div>
          </TabsContent>

          {/* ── costs ── */}
          <TabsContent value="costs" className="qb-view">
            <Panel
              title="Cost with no client attached"
              meta={
                data.unattached_cost
                  ? `${data.unattached_cost.unattached_count.toLocaleString()} of ${data.unattached_cost.purchase_count.toLocaleString()} purchases`
                  : undefined
              }
            >
              {data.unattached_cost ? (
                <>
                  <Figure
                    label="Billable cost with nowhere to land"
                    size="lg"
                    tone="out"
                    value={usd(data.unattached_cost.cost_of_service_unattached)}
                    sub={`${Math.round(data.unattached_cost.unattached_pct)}% of purchases are untagged`}
                  />
                  <DataTable
                    data={data.unattached_cost.accounts}
                    pageSize={6}
                    initialSort="amount"
                    columns={[
                      {
                        accessorKey: "account",
                        header: "Account",
                        cell: (c) => (
                          <>
                            <span className="qb-name">{c.getValue<string>()}</span>
                            {c.row.original.is_cost_of_service ? (
                              <span className="qb-tag">billable</span>
                            ) : null}
                          </>
                        ),
                      },
                      {
                        accessorKey: "amount",
                        header: "Amount",
                        meta: { numeric: true },
                        cell: (c) => usd(c.getValue<number>()),
                      },
                    ]}
                  />
                </>
              ) : (
                <Empty>Purchase attribution is unavailable.</Empty>
              )}
            </Panel>

            <div className="qb-two">
              <Panel
                title="Spend by vendor"
                meta={
                  data.expenses_by_vendor
                    ? `${usd(data.expenses_by_vendor.total)} across ${data.expenses_by_vendor.vendor_count} vendors`
                    : undefined
                }
              >
                {data.expenses_by_vendor?.vendors?.length ? (
                  <DataTable
                    data={data.expenses_by_vendor.vendors}
                    pageSize={8}
                    initialSort="amount"
                    columns={[
                      {
                        accessorKey: "vendor",
                        header: "Vendor",
                        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
                      },
                      {
                        accessorKey: "amount",
                        header: "Spend",
                        meta: { numeric: true },
                        cell: (c) => usd(c.getValue<number>()),
                      },
                    ]}
                  />
                ) : (
                  <Empty>Vendor spend is unavailable.</Empty>
                )}
              </Panel>

              <Panel
                title="Committed and paid"
                meta={data.purchase_orders ? `${data.purchase_orders.open_count} open POs` : undefined}
              >
                <div className="qb-pair">
                  {data.purchase_orders ? (
                    <Figure
                      label="Open purchase orders"
                      value={usd(data.purchase_orders.open_total)}
                      sub={`${usd(data.purchase_orders.ytd_total)} ordered this year`}
                    />
                  ) : null}
                  {data.bill_payments ? (
                    <Figure
                      label="Bills paid"
                      value={usd(data.bill_payments.total_paid)}
                      sub={`${data.bill_payments.payment_count} payments`}
                    />
                  ) : null}
                </div>
                {data.purchase_orders?.vendors?.length ? (
                  <DataTable
                    data={data.purchase_orders.vendors}
                    pageSize={5}
                    initialSort="amount"
                    columns={[
                      {
                        accessorKey: "vendor",
                        header: "Vendor",
                        cell: (c) => <span className="qb-name">{c.getValue<string>()}</span>,
                      },
                      {
                        accessorKey: "amount",
                        header: "Ordered",
                        meta: { numeric: true },
                        cell: (c) => usd(c.getValue<number>()),
                      },
                    ]}
                  />
                ) : (
                  <Empty>No purchase orders on file.</Empty>
                )}
              </Panel>
            </div>
          </TabsContent>
            </>
          ) : null}
        </Tabs>

        <AiIntelligenceDrawer
          open={aiOpen}
          onClose={() => setAiOpen(false)}
          insights={insights}
          chat={chat}
          onGo={setView}
        />
      </div>
    </TooltipProvider>
  );
}

/* ── segment matrix ────────────────────────────────────────────────────── */

function SegmentMatrix({ rc }: { rc: NonNullable<QuickBooksOverview["revenue_by_class"]> }) {
  const max = Math.max(...rc.matrix.map((c) => c.amount), 1);
  const cell = (parent: string, segment: string) =>
    rc.matrix.find((c) => c.parent === parent && c.segment === segment);

  return (
    <>
      <div className="qb-scroll">
        <table className="qb-matrix">
          <thead>
            <tr>
              <th scope="col">
                <span className="qb-sr">Line of business</span>
              </th>
              {rc.segments.map((s) => (
                <th key={s} scope="col">
                  {s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rc.parents.map((p) => (
              <tr key={p}>
                <th scope="row">{p.replace(/ Revenue$/, "")}</th>
                {rc.segments.map((s) => {
                  const c = cell(p, s);
                  const heat = (c?.amount ?? 0) / max;
                  return (
                    <td
                      key={`${p}-${s}`}
                      style={{
                        // Capped so the darkest cell still holds 4.5:1 against
                        // the body colour — no white-on-mid-tone flip.
                        background: `color-mix(in srgb, var(--zo-teal) ${Math.round(8 + heat * 38)}%, var(--zo-card-bg))`,
                      }}
                    >
                      {c ? compact(c.amount) : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rc.unclassified > 0 ? (
        <Note>{usd(rc.unclassified)} of income has no segment assigned.</Note>
      ) : null}
    </>
  );
}
