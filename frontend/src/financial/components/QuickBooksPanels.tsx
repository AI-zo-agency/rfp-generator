"use client";

import { useEffect, useMemo, useState } from "react";
import { FadeIn } from "@/components/ui/FadeIn";
import { AnimatedNumber } from "./AnimatedNumber";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

/* ── types ─────────────────────────────────────────────────────────────── */

type Bucket = { label: string; amount: number; count?: number; pct?: number };

export interface QuickBooksOverview {
  year: number;
  generated_at: string;
  errors: Record<string, string>;
  company: { company_name: string; legal_name: string; city: string; state: string; sku: string } | null;
  ar: {
    total: number; invoice_count: number; overdue_total: number;
    buckets: Bucket[];
    clients: { client: string; amount: number; invoices: number; oldest_days: number }[];
  } | null;
  ap: { total: number; bill_count: number; buckets: Bucket[]; vendors: { vendor: string; amount: number }[] } | null;
  revenue_by_class: {
    matrix: { parent: string; segment: string; amount: number }[];
    parents: string[];
    segments: string[];
    unclassified: number;
    total: number;
    coverage_pct: number;
  } | null;
  by_account_manager: { managers: { manager: string; income: number; net: number; is_overhead: boolean }[] } | null;
  client_profitability: { clients: { client: string; income: number; expense: number; net: number; margin_pct: number | null }[]; attributed_expense: number } | null;
  monthly_trend: { months: { month: string; amount: number }[]; total: number; peak: number; last_booked_month: string | null } | null;
  unattached_cost: {
    purchase_count: number; purchase_total: number; unattached_count: number; unattached_pct: number;
    cost_of_service_unattached: number;
    accounts: { account: string; amount: number; is_cost_of_service: boolean }[];
  } | null;
  activity: { since: string; total: number; entities: { entity: string; changed: number }[] } | null;
}

/* ── formatting ────────────────────────────────────────────────────────── */

const usd = (n: number, digits = 0) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Group separators without the currency symbol — the symbol is the animator's prefix. */
const grouped = (n: number) =>
  Math.round(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

const compact = (n: number) =>
  n >= 1000 ? `$${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : usd(n);

/* Aging severity — deliberately not the brand accent. */
const AGE_TONE: Record<string, string> = {
  "Not yet due": "var(--zo-teal)",
  "1-30 days": "var(--zo-olive)",
  "31-60 days": "var(--zo-yellow)",
  "61-90 days": "var(--zo-orange-soft)",
  "90+ days": "var(--zo-danger)",
};

/* ── shared chrome ─────────────────────────────────────────────────────── */

function Panel({
  title, hint, children, span = "",
}: { title: string; hint?: string; children: React.ReactNode; span?: string }) {
  return (
    <section
      className={`flex flex-col rounded-2xl border border-[var(--zo-border)] bg-[var(--zo-card-bg)] p-5 shadow-[var(--zo-card-shadow)] sm:p-6 ${span}`}
    >
      <div className="mb-5 flex items-baseline justify-between gap-3">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.18em] text-[var(--zo-text-muted)]">
          {title}
        </h3>
        {hint ? (
          <span className="text-[11px] tabular-nums text-[var(--zo-text-muted)]">{hint}</span>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function Stat({
  label, value, sub, tone = "var(--zo-text)", decimals = 0,
}: { label: string; value: number; sub?: string; tone?: string; decimals?: number }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-2xl border border-[var(--zo-border)] bg-[var(--zo-card-bg)] p-5 shadow-[var(--zo-card-shadow)]">
      <span className="text-[11px] font-bold uppercase tracking-[0.16em] text-[var(--zo-text-muted)]">
        {label}
      </span>
      <AnimatedNumber
        value={value}
        prefix={value < 0 ? "-$" : "$"}
        decimals={decimals}
        format={(n) => grouped(Math.abs(n))}
        className="font-heading text-[1.75rem] leading-none tabular-nums sm:text-[2.1rem]"
      />
      {sub ? <span className="text-xs text-[var(--zo-text-secondary)]">{sub}</span> : null}
      <span aria-hidden className="mt-1 h-[3px] w-10 rounded-full" style={{ background: tone }} />
    </div>
  );
}

function Empty({ what }: { what: string }) {
  return (
    <p className="py-6 text-center text-sm text-[var(--zo-text-muted)]">
      {what} unavailable — check the QuickBooks connection.
    </p>
  );
}

/* ── monthly trend: bars, faint grid, emphasized final month ───────────── */

function TrendChart({ months, peak }: { months: { month: string; amount: number }[]; peak: number }) {
  const W = 760;
  const H = 190;
  const pad = { l: 4, r: 4, t: 14, b: 24 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const slot = innerW / Math.max(months.length, 1);
  const barW = Math.min(slot * 0.56, 42);
  const lastBooked = months.reduce((acc, m, i) => (m.amount > 0 ? i : acc), -1);

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Booked revenue by month for the year, from the QuickBooks ledger" className="h-auto w-full min-w-[560px]">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line
            key={f}
            x1={pad.l} x2={W - pad.r}
            y1={pad.t + innerH * (1 - f)} y2={pad.t + innerH * (1 - f)}
            stroke="var(--zo-border)" strokeWidth="1"
          />
        ))}
        {months.map((m, i) => {
          const h = peak ? (m.amount / peak) * innerH : 0;
          const x = pad.l + slot * i + (slot - barW) / 2;
          const y = pad.t + innerH - h;
          const isLast = i === lastBooked;
          const empty = m.amount === 0;
          return (
            <g key={m.month}>
              {empty ? (
                <rect x={x} y={pad.t + innerH - 3} width={barW} height={3} rx={1.5} fill="var(--zo-border)" />
              ) : (
                <rect
                  x={x} y={y} width={barW} height={Math.max(h, 2)} rx={3}
                  fill={isLast ? "var(--zo-orange)" : "var(--zo-teal)"}
                  opacity={isLast ? 1 : 0.82}
                />
              )}
              {isLast ? (
                <text x={x + barW / 2} y={y - 5} textAnchor="middle" className="fill-[var(--zo-orange)]" style={{ fontSize: 11, fontWeight: 700 }}>
                  {compact(m.amount)}
                </text>
              ) : null}
              <text
                x={x + barW / 2} y={H - 7} textAnchor="middle"
                className="fill-[var(--zo-text-muted)]" style={{ fontSize: 10 }}
              >
                {m.month.split(" ")[0]}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ── panels ────────────────────────────────────────────────────────────── */

function ArPanel({ ar }: { ar: NonNullable<QuickBooksOverview["ar"]> }) {
  const total = ar.total || 1;
  return (
    <>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-[var(--zo-surface)]">
        {ar.buckets.filter((b) => b.amount > 0).map((b) => (
          <div
            key={b.label}
            title={`${b.label} — ${usd(b.amount)}`}
            style={{ width: `${(b.amount / total) * 100}%`, background: AGE_TONE[b.label] }}
          />
        ))}
      </div>
      <div className="mt-4 grid gap-x-5 gap-y-2 sm:grid-cols-2">
        {ar.buckets.filter((b) => b.amount > 0).map((b) => (
          <div key={b.label} className="flex items-center gap-2.5 text-sm">
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: AGE_TONE[b.label] }} />
            <span className="text-[var(--zo-text-secondary)]">{b.label}</span>
            <span className="ml-auto tabular-nums font-medium">{usd(b.amount)}</span>
            <span className="w-12 text-right text-xs tabular-nums text-[var(--zo-text-muted)]">{b.count} inv</span>
          </div>
        ))}
      </div>

      <div className="mt-6 border-t border-[var(--zo-border)] pt-4">
        <p className="mb-3 text-[11px] font-bold uppercase tracking-[0.16em] text-[var(--zo-text-muted)]">
          Who owes it
        </p>
        <ul className="flex flex-col gap-2.5">
          {ar.clients.slice(0, 6).map((c) => (
            <li key={c.client} className="flex items-center gap-3 text-sm">
              <span className="min-w-0 flex-1 truncate" title={c.client}>{c.client}</span>
              {c.oldest_days > 30 ? (
                <span className="shrink-0 rounded-full bg-[var(--zo-danger)]/10 px-2 py-0.5 text-[10px] font-bold tabular-nums text-[var(--zo-danger)]">
                  {c.oldest_days}d
                </span>
              ) : null}
              <span className="shrink-0 tabular-nums font-medium">{usd(c.amount)}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function ClassPanel({ rc }: { rc: NonNullable<QuickBooksOverview["revenue_by_class"]> }) {
  // A stale or partial payload shouldn't take the page down with it.
  if (!Array.isArray(rc.matrix) || rc.matrix.length === 0) return <Empty what="Segment split" />;
  const max = Math.max(...rc.matrix.map((c) => c.amount), 1);
  const cell = (parent: string, segment: string) =>
    rc.matrix.find((c) => c.parent === parent && c.segment === segment);

  return (
    <>
      {/* engagement type × client segment — the 2×2 the ledger is built around */}
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `minmax(84px, 0.8fr) repeat(${rc.segments.length}, minmax(0, 1fr))` }}
      >
        <span aria-hidden />
        {rc.segments.map((s) => (
          <span key={s} className="pb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--zo-text-muted)]">
            {s}
          </span>
        ))}

        {rc.parents.map((p) => (
          <div key={p} className="contents">
            <span className="flex items-center pr-2 text-[11px] font-bold uppercase leading-tight tracking-[0.1em] text-[var(--zo-text-muted)]">
              {p.replace(/ Revenue$/, "")}
            </span>
            {rc.segments.map((s) => {
              const c = cell(p, s);
              return (
                <div key={`${p}-${s}`} className="rounded-xl bg-[var(--zo-surface)] p-3.5">
                  <span className="block font-heading text-lg leading-none tabular-nums">
                    {c ? compact(c.amount) : "—"}
                  </span>
                  <span
                    aria-hidden
                    className="mt-2.5 block h-1 rounded-full bg-[var(--zo-teal)]"
                    style={{ width: `${((c?.amount ?? 0) / max) * 100}%` }}
                  />
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div className="mt-5 rounded-xl border border-[var(--zo-yellow)]/45 bg-[var(--zo-yellow)]/10 p-4">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium">Revenue with no segment</span>
          <span className="tabular-nums font-heading text-lg">{usd(rc.unclassified)}</span>
        </div>
        <p className="mt-1.5 text-xs text-[var(--zo-text-secondary)]">
          {rc.coverage_pct}% of income is classified. The rest can’t be split Government vs Private.
        </p>
      </div>
    </>
  );
}

function ManagerPanel({ am }: { am: NonNullable<QuickBooksOverview["by_account_manager"]> }) {
  const owners = am.managers.filter((m) => !m.is_overhead && m.income > 0);
  const max = Math.max(...owners.map((m) => m.income), 1);
  return (
    <ul className="flex flex-col gap-4">
      {owners.map((m) => (
        <li key={m.manager}>
          <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
            <span className="font-medium">{m.manager}</span>
            <span className="tabular-nums">{usd(m.income)}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-[var(--zo-surface)]">
            <div className="h-full rounded-full bg-[var(--zo-orange)]" style={{ width: `${(m.income / max) * 100}%` }} />
          </div>
        </li>
      ))}
      {owners.length === 0 ? <Empty what="Account manager split" /> : null}
    </ul>
  );
}

function ClientTable({ cp }: { cp: NonNullable<QuickBooksOverview["client_profitability"]> }) {
  return (
    <>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full min-w-[420px] text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-[0.14em] text-[var(--zo-text-muted)]">
              <th className="pb-2 pr-3 font-bold">Client</th>
              <th className="pb-2 px-2 text-right font-bold">Income</th>
              <th className="pb-2 px-2 text-right font-bold">Cost</th>
              <th className="pb-2 pl-2 text-right font-bold">Net</th>
            </tr>
          </thead>
          <tbody>
            {cp.clients.slice(0, 9).map((c) => (
              <tr key={c.client} className="border-t border-[var(--zo-border)]">
                <td className="max-w-[200px] truncate py-2.5 pr-3" title={c.client}>{c.client}</td>
                <td className="px-2 py-2.5 text-right tabular-nums">{compact(c.income)}</td>
                <td className="px-2 py-2.5 text-right tabular-nums text-[var(--zo-text-muted)]">
                  {c.expense ? compact(c.expense) : "—"}
                </td>
                <td className="py-2.5 pl-2 text-right tabular-nums font-medium">{compact(c.net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 rounded-lg bg-[var(--zo-surface)] p-3 text-xs leading-relaxed text-[var(--zo-text-secondary)]">
        Only {usd(cp.attributed_expense)} of cost is tagged to a client, so these margins read high.
        Attribution — not profitability — is what this panel is really measuring today.
      </p>
    </>
  );
}

function UnattachedPanel({ uc }: { uc: NonNullable<QuickBooksOverview["unattached_cost"]> }) {
  const max = Math.max(...uc.accounts.map((a) => a.amount), 1);
  return (
    <>
      <div className="mb-5 flex flex-wrap items-end gap-x-8 gap-y-3">
        <div>
          <AnimatedNumber
            value={uc.cost_of_service_unattached}
            prefix="$"
            format={grouped}
            className="font-heading text-[2rem] leading-none tabular-nums text-[var(--zo-danger)]"
          />
          <p className="mt-1 text-xs text-[var(--zo-text-secondary)]">cost of services with no client</p>
        </div>
        <div className="text-sm tabular-nums text-[var(--zo-text-muted)]">
          {uc.unattached_count.toLocaleString()} of {uc.purchase_count.toLocaleString()} purchases ({uc.unattached_pct}%)
        </div>
      </div>
      <ul className="flex flex-col gap-3">
        {uc.accounts.slice(0, 7).map((a) => (
          <li key={a.account}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate" title={a.account}>
                {a.account}
                {a.is_cost_of_service ? (
                  <span className="ml-2 rounded bg-[var(--zo-danger)]/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-[var(--zo-danger)]">
                    billable
                  </span>
                ) : null}
              </span>
              <span className="shrink-0 tabular-nums">{compact(a.amount)}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-[var(--zo-surface)]">
              <div
                className="h-full rounded-full"
                style={{ width: `${(a.amount / max) * 100}%`, background: a.is_cost_of_service ? "var(--zo-danger)" : "var(--zo-border)" }}
              />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

/* ── container ─────────────────────────────────────────────────────────── */

export function QuickBooksPanels() {
  const [data, setData] = useState<QuickBooksOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/financials/quickbooks/overview${refresh ? "?refresh=true" : ""}`);
      if (!res.ok) throw new Error(`QuickBooks returned ${res.status}`);
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach QuickBooks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const net = useMemo(
    () => (data?.ar?.total ?? 0) - (data?.ap?.total ?? 0),
    [data?.ar?.total, data?.ap?.total],
  );

  if (loading && !data) {
    return (
      <div className="flex flex-col items-center gap-4 py-24">
        <div className="h-9 w-9 animate-spin rounded-full border-[3px] border-[var(--zo-teal)] border-t-transparent" />
        <p className="text-sm text-[var(--zo-text-muted)]">Reading the ledger…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-2xl border border-[var(--zo-danger)]/30 bg-[var(--zo-danger)]/[0.05] p-8 text-center">
        <p className="font-medium text-[var(--zo-danger)]">{error ?? "No QuickBooks data"}</p>
        <button type="button" onClick={() => void load(true)} className="zo-btn secondary mt-4 cursor-pointer">
          Retry
        </button>
      </div>
    );
  }

  const trend = data.monthly_trend;

  return (
    <FadeIn>
      <div className="flex flex-col gap-5">
        {/* ledger identity + refresh */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2.5 text-[11px] uppercase tracking-[0.16em] text-[var(--zo-text-muted)]">
            <span className="inline-flex items-center gap-2 rounded-full border border-[var(--zo-teal)]/30 bg-[var(--zo-teal)]/[0.07] px-3 py-1.5 font-bold text-[var(--zo-teal)]">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--zo-teal)]" />
              Live · read-only
            </span>
            {data.company ? <span>{data.company.legal_name}</span> : null}
            {data.company ? <span>{data.company.sku}</span> : null}
          </div>
          <button type="button" onClick={() => void load(true)} className="zo-btn secondary !py-2.5 cursor-pointer text-xs" disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        {/* headline stats */}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Owed to zö" value={data.ar?.total ?? 0} tone="var(--zo-orange)"
                sub={`${data.ar?.invoice_count ?? 0} open · ${usd(data.ar?.overdue_total ?? 0)} overdue`} />
          <Stat label="zö owes" value={data.ap?.total ?? 0} tone="var(--zo-olive)"
                sub={`${data.ap?.bill_count ?? 0} unpaid bills`} />
          <Stat label="Net position" value={net} tone={net >= 0 ? "var(--zo-teal)" : "var(--zo-danger)"}
                sub="receivables less payables" />
          <Stat label={`Booked ${data.year}`} value={trend?.total ?? 0} tone="var(--zo-teal)"
                sub={trend?.last_booked_month ? `last booked ${trend.last_booked_month}` : undefined} />
        </div>

        {/* trend */}
        <Panel title="Booked revenue by month" hint={trend?.last_booked_month ? `nothing booked after ${trend.last_booked_month}` : undefined}>
          {trend ? <TrendChart months={trend.months} peak={trend.peak} /> : <Empty what="Monthly trend" />}
        </Panel>

        <div className="grid gap-5 lg:grid-cols-2">
          <Panel title="Receivables by age" hint={`${data.ar?.invoice_count ?? 0} invoices`}>
            {data.ar ? <ArPanel ar={data.ar} /> : <Empty what="Receivables" />}
          </Panel>

          <Panel title={`Revenue by segment · ${data.year}`} hint={data.revenue_by_class ? `${data.revenue_by_class.coverage_pct}% classified` : undefined}>
            {data.revenue_by_class ? <ClassPanel rc={data.revenue_by_class} /> : <Empty what="Segment split" />}
          </Panel>

          <Panel title="Revenue by account manager" hint="Department field">
            {data.by_account_manager ? <ManagerPanel am={data.by_account_manager} /> : <Empty what="Account manager split" />}
          </Panel>

          <Panel title={`Client profitability · ${data.year}`}>
            {data.client_profitability ? <ClientTable cp={data.client_profitability} /> : <Empty what="Client profitability" />}
          </Panel>

          <Panel title="Cost with no client attached" span="lg:col-span-2">
            {data.unattached_cost ? <UnattachedPanel uc={data.unattached_cost} /> : <Empty what="Unattached cost" />}
          </Panel>
        </div>

        {/* owed to vendors + activity */}
        <div className="grid gap-5 lg:grid-cols-2">
          <Panel title="Owed to vendors" hint={`${data.ap?.bill_count ?? 0} bills`}>
            {data.ap ? (
              <ul className="flex flex-col gap-2.5">
                {data.ap.vendors.slice(0, 7).map((v) => (
                  <li key={v.vendor} className="flex items-center gap-3 text-sm">
                    <span className="min-w-0 flex-1 truncate" title={v.vendor}>{v.vendor}</span>
                    <span className="shrink-0 tabular-nums font-medium">{usd(v.amount)}</span>
                  </li>
                ))}
              </ul>
            ) : <Empty what="Payables" />}
          </Panel>

          <Panel title="Ledger activity" hint={data.activity ? `${data.activity.total} changes` : undefined}>
            {data.activity ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {data.activity.entities.map((e) => (
                  <div key={e.entity} className="rounded-xl bg-[var(--zo-surface)] p-3">
                    <span className="block font-heading text-xl tabular-nums">{e.changed}</span>
                    <span className="text-[11px] text-[var(--zo-text-muted)]">{e.entity}</span>
                  </div>
                ))}
              </div>
            ) : <Empty what="Activity feed" />}
          </Panel>
        </div>

        {Object.keys(data.errors ?? {}).length > 0 ? (
          <p className="text-xs text-[var(--zo-text-muted)]">
            Some panels did not load: {Object.keys(data.errors).join(", ")}.
          </p>
        ) : null}
      </div>
    </FadeIn>
  );
}
