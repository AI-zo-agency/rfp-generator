"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Info } from "lucide-react";
import { FadeIn } from "@/components/ui/FadeIn";
import { AnimatedNumber } from "./AnimatedNumber";
import "./QuickBooksLedger.css";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

/* ── types ─────────────────────────────────────────────────────────────── */

type Bucket = { label: string; amount: number; count?: number; pct?: number };
type MonthAmt = { month: string; amount: number };

export interface QuickBooksOverview {
  year: number;
  generated_at: string;
  as_of?: string;
  synced_at?: string;
  sync_status?: "ok" | "failed" | "backfill_pending" | "missing";
  errors: Record<string, string>;
  company: {
    company_name: string;
    legal_name: string;
    city: string;
    state: string;
    sku: string;
  } | null;
  ar: {
    total: number;
    invoice_count: number;
    overdue_total: number;
    buckets: Bucket[];
    clients: { client: string; amount: number; invoices: number; oldest_days: number }[];
  } | null;
  ap: {
    total: number;
    bill_count: number;
    buckets: Bucket[];
    vendors: { vendor: string; amount: number }[];
  } | null;
  revenue_by_class: {
    matrix: { parent: string; segment: string; amount: number }[];
    parents: string[];
    segments: string[];
    unclassified: number;
    total: number;
    coverage_pct: number;
  } | null;
  by_account_manager: {
    managers: { manager: string; income: number; net: number; is_overhead: boolean }[];
  } | null;
  client_profitability: {
    clients: {
      client: string;
      income: number;
      expense: number;
      net: number;
      margin_pct: number | null;
    }[];
    attributed_expense: number;
  } | null;
  monthly_trend: {
    months: MonthAmt[];
    total: number;
    peak: number;
    last_booked_month: string | null;
  } | null;
  unattached_cost: {
    purchase_count: number;
    purchase_total: number;
    unattached_count: number;
    unattached_pct: number;
    cost_of_service_unattached: number;
    accounts: { account: string; amount: number; is_cost_of_service: boolean }[];
  } | null;
  activity: {
    since: string;
    total: number;
    entities: { entity: string; changed: number }[];
  } | null;
  cash_collections: {
    total_collected: number;
    payment_count: number;
    by_month: MonthAmt[];
    top_payers: { customer: string; amount: number }[];
  } | null;
  billing_vs_cash: {
    invoiced_total: number;
    collected_total: number;
    open_ar: number;
    collection_rate_pct: number;
    invoice_count: number;
    payment_count: number;
    by_month: { month: string; invoiced: number; collected: number }[];
  } | null;
  dso: {
    dso_days: number | null;
    sample_size: number;
    slowest_clients: { client: string; avg_days: number; amount: number }[];
  } | null;
  aged_ar_detail: {
    report_date: string;
    columns: string[];
    row_count: number;
    source: string;
  } | null;
  purchase_orders: {
    po_count: number;
    open_count: number;
    open_total: number;
    ytd_total: number;
    vendors: { vendor: string; amount: number }[];
  } | null;
  expenses_by_vendor: {
    total: number;
    vendor_count: number;
    top3_concentration_pct: number;
    vendors: { vendor: string; amount: number }[];
  } | null;
  bill_payments: {
    total_paid: number;
    payment_count: number;
    by_month: MonthAmt[];
  } | null;
  customers: {
    count: number;
    customers: { id: string; display_name: string; company_name: string; balance: number }[];
  } | null;
  sales_by_customer: {
    total: number;
    clients: { client: string; amount: number }[];
  } | null;
  credit_memos: {
    total: number;
    count: number;
    clients: { client: string; amount: number }[];
  } | null;
  class_coverage: {
    class_count: number;
    classes: string[];
    coverage_pct: number;
    unclassified: number;
    total: number;
  } | null;
  department_coverage: {
    department_count: number;
    departments: string[];
    overhead_income: number;
    overhead_pct: number;
    manager_count: number;
  } | null;
  liquidity: {
    as_of: string;
    cash: number;
    net_cash_change: number | null;
  } | null;
}

const SECTIONS = [
  { id: "health", label: "Health" },
  { id: "cash", label: "Cash" },
  { id: "receivables", label: "Receivables" },
  { id: "payables", label: "Payables" },
  { id: "revenue", label: "Revenue" },
  { id: "clients", label: "Clients" },
  { id: "costs", label: "Costs" },
  { id: "activity", label: "Activity" },
] as const;

/* ── formatting ────────────────────────────────────────────────────────── */

const usd = (n: number, digits = 0) =>
  n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

const grouped = (n: number) =>
  Math.round(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

const compact = (n: number) =>
  n >= 1000 ? `$${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : usd(n);

const AGE_TONE: Record<string, string> = {
  "Not yet due": "var(--zo-teal)",
  "1-30 days": "var(--zo-olive)",
  "31-60 days": "var(--zo-yellow)",
  "61-90 days": "var(--zo-orange-soft)",
  "90+ days": "var(--zo-danger)",
};

/* ── shared chrome ─────────────────────────────────────────────────────── */

function Section({
  id,
  title,
  hint,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="qb-section">
      <div className="qb-section-head">
        <h2>{title}</h2>
        {hint ? <span className="qb-hint">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

function Widget({ children, padded = true }: { children: React.ReactNode; padded?: boolean }) {
  return <div className={`qb-widget${padded ? " qb-widget-pad" : ""}`}>{children}</div>;
}

function Kicker({ children }: { children: React.ReactNode }) {
  return <p className="qb-kicker">{children}</p>;
}

function Empty({ what }: { what: string }) {
  return <p className="qb-empty">{what}</p>;
}

function ShowMoreList<T>({
  items,
  initial = 6,
  head,
  render,
}: {
  items: T[];
  initial?: number;
  head?: React.ReactNode;
  render: (item: T, index: number) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const visible = open ? items : items.slice(0, initial);
  return (
    <div>
      {head}
      <ul className="qb-group">{visible.map(render)}</ul>
      {items.length > initial ? (
        <button type="button" onClick={() => setOpen((v) => !v)} className="qb-more">
          {open ? "Show less" : `Show all ${items.length}`}
        </button>
      ) : null}
    </div>
  );
}

function InfoTip({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const delayRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);

  const cancel = () => {
    if (delayRef.current) {
      clearTimeout(delayRef.current);
      delayRef.current = null;
    }
  };

  const show = () => {
    cancel();
    delayRef.current = setTimeout(() => setOpen(true), 140);
  };

  const hide = () => {
    cancel();
    if (!pinned) setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setPinned(false);
      setOpen(false);
    };
    const onPointer = (e: PointerEvent) => {
      if (wrapRef.current?.contains(e.target as Node)) return;
      setPinned(false);
      setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  useEffect(() => () => cancel(), []);

  return (
    <span
      ref={wrapRef}
      className="qb-help"
      onPointerEnter={show}
      onPointerLeave={hide}
    >
      <button
        type="button"
        className="qb-help-btn"
        aria-label={label}
        aria-expanded={open}
        aria-controls="qb-lag-help"
        onClick={() => {
          cancel();
          if (pinned) {
            setPinned(false);
            setOpen(false);
          } else {
            setPinned(true);
            setOpen(true);
          }
        }}
      >
        <Info size={13} strokeWidth={2.25} aria-hidden />
      </button>
      <span
        id="qb-lag-help"
        role="tooltip"
        className="qb-help-pop"
        data-open={open ? "true" : "false"}
        hidden={!open}
      >
        {children}
      </span>
    </span>
  );
}

function SectionNav({ active }: { active: string }) {
  const trackRef = useRef<HTMLDivElement>(null);
  const linkRefs = useRef<Map<string, HTMLAnchorElement>>(new Map());
  const [thumb, setThumb] = useState({ x: 0, w: 0 });

  const measure = useCallback(() => {
    const track = trackRef.current;
    const el = linkRefs.current.get(active);
    if (!track || !el) return;
    const tr = track.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    setThumb({ x: er.left - tr.left + track.scrollLeft, w: er.width });
  }, [active]);

  useLayoutEffect(() => {
    measure();
    const track = trackRef.current;
    if (!track) return;
    const ro = new ResizeObserver(measure);
    ro.observe(track);
    track.addEventListener("scroll", measure, { passive: true });
    return () => {
      ro.disconnect();
      track.removeEventListener("scroll", measure);
    };
  }, [measure]);

  return (
    <nav aria-label="Ledger sections" className="qb-nav">
      <div ref={trackRef} className="qb-nav-track">
        <span
          aria-hidden
          className="qb-nav-thumb"
          style={{
            width: thumb.w,
            transform: `translateX(${thumb.x}px)`,
            opacity: thumb.w ? 1 : 0,
          }}
        />
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            data-active={active === s.id}
            ref={(node) => {
              if (node) linkRefs.current.set(s.id, node);
              else linkRefs.current.delete(s.id);
            }}
            onClick={(e) => {
              e.preventDefault();
              document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
          >
            {s.label}
          </a>
        ))}
      </div>
    </nav>
  );
}

function YearSeg({
  year,
  years,
  onChange,
}: {
  year: number;
  years: number[];
  onChange: (y: number) => void;
}) {
  const idx = Math.max(0, years.indexOf(year));
  return (
    <div
      className="qb-seg"
      role="radiogroup"
      aria-label="Year"
      tabIndex={0}
      style={{ ["--qb-years" as string]: String(years.length) }}
      onKeyDown={(e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        const next = idx + (e.key === "ArrowRight" ? 1 : -1);
        if (next >= 0 && next < years.length) onChange(years[next]);
      }}
    >
      <span
        aria-hidden
        className="qb-seg-thumb"
        style={{ width: `calc((100% - 6px) / ${years.length})`, transform: `translateX(${idx * 100}%)` }}
      />
      {years.map((y) => (
        <button
          key={y}
          type="button"
          role="radio"
          tabIndex={-1}
          aria-checked={y === year}
          onClick={() => onChange(y)}
        >
          {y}
        </button>
      ))}
    </div>
  );
}

function HealthStrip({
  ar,
  ap,
  net,
  booked,
  year,
  lastBooked,
  liquidity,
}: {
  ar: number;
  ap: number;
  net: number;
  booked: number;
  year: number;
  lastBooked?: string | null;
  liquidity: QuickBooksOverview["liquidity"];
}) {
  const metrics: { label: string; value: number; tone: string; sub?: string }[] = [
    { label: "Owed to zö", value: ar, tone: "qb-tone-out" },
    { label: "zö owes", value: ap, tone: "qb-tone-due" },
    {
      label: "Net position",
      value: net,
      tone: net >= 0 ? "qb-tone-ok" : "qb-tone-warn",
    },
    {
      label: `Booked ${year}`,
      value: booked,
      tone: "",
      sub: lastBooked ? `Through ${lastBooked}` : undefined,
    },
  ];
  if (liquidity && (liquidity.cash || liquidity.net_cash_change != null)) {
    metrics.push({
      label: "Cash on hand",
      value: liquidity.cash,
      tone: "qb-tone-in",
      sub:
        liquidity.net_cash_change != null
          ? `YTD change ${usd(liquidity.net_cash_change)}`
          : undefined,
    });
  }

  return (
    <div className="qb-widget qb-health" style={{ ["--qb-cols" as string]: String(metrics.length) }}>
      {metrics.map((m) => (
        <div key={m.label} className="qb-metric">
          <span className="qb-metric-label">{m.label}</span>
          <AnimatedNumber
            value={m.value}
            prefix={m.value < 0 ? "-$" : "$"}
            format={(n) => grouped(Math.abs(n))}
            className={`qb-metric-value ${m.tone}`}
          />
          {m.sub ? <span className="qb-metric-sub">{m.sub}</span> : null}
        </div>
      ))}
    </div>
  );
}

function LedgerSkeleton() {
  return (
    <div className="qb-skel" aria-busy="true" aria-label="Reading the ledger">
      <div className="qb-skel-bar" />
      <div className="qb-skel-block" />
      <div className="qb-skel-block" style={{ height: 220 }} />
      <div className="qb-skel-block" style={{ height: 160 }} />
    </div>
  );
}

/* ── charts ────────────────────────────────────────────────────────────── */

function TrendChart({ months, peak }: { months: MonthAmt[]; peak: number }) {
  const W = 760;
  const H = 190;
  const pad = { l: 4, r: 4, t: 14, b: 24 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const slot = innerW / Math.max(months.length, 1);
  const barW = Math.min(slot * 0.56, 42);
  const lastBooked = months.reduce((acc, m, i) => (m.amount > 0 ? i : acc), -1);

  return (
    <div className="qb-scroll">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Booked revenue by month"
        className="h-auto w-full min-w-[560px]"
      >
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line
            key={f}
            x1={pad.l}
            x2={W - pad.r}
            y1={pad.t + innerH * (1 - f)}
            y2={pad.t + innerH * (1 - f)}
            stroke="var(--zo-border)"
            strokeWidth="1"
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
                <rect
                  x={x}
                  y={pad.t + innerH - 3}
                  width={barW}
                  height={3}
                  rx={1.5}
                  fill="var(--zo-border)"
                />
              ) : (
                <rect
                  x={x}
                  y={y}
                  width={barW}
                  height={Math.max(h, 2)}
                  rx={3}
                  fill={isLast ? "var(--zo-orange)" : "var(--zo-teal)"}
                  opacity={isLast ? 1 : 0.82}
                />
              )}
              {isLast ? (
                <text
                  x={x + barW / 2}
                  y={y - 5}
                  textAnchor="middle"
                  className="fill-[var(--zo-orange)]"
                  style={{ fontSize: 11, fontWeight: 700 }}
                >
                  {compact(m.amount)}
                </text>
              ) : null}
              <text
                x={x + barW / 2}
                y={H - 7}
                textAnchor="middle"
                className="fill-[var(--zo-text-muted)]"
                style={{ fontSize: 10 }}
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

function DualMonthChart({
  rows,
}: {
  rows: { month: string; invoiced: number; collected: number }[];
}) {
  const peak = Math.max(...rows.flatMap((r) => [r.invoiced, r.collected]), 1);
  const W = 760;
  const H = 188;
  const pad = { l: 4, r: 4, t: 16, b: 26 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const slot = innerW / Math.max(rows.length, 1);
  const barW = Math.min(slot * 0.3, 16);

  const plotRef = useRef<HTMLDivElement>(null);
  const delayRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const coolRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const instantRef = useRef(false);
  const openRef = useRef(false);

  const [tip, setTip] = useState<{
    index: number;
    series: "invoiced" | "collected" | null;
    x: number;
  } | null>(null);
  const [open, setOpen] = useState(false);
  const [instant, setInstant] = useState(false);

  const place = useCallback(
    (index: number, series: "invoiced" | "collected" | null, el: Element, nextInstant: boolean) => {
      const plot = plotRef.current;
      if (!plot) return;
      const pb = plot.getBoundingClientRect();
      const rb = el.getBoundingClientRect();
      const x = Math.min(
        Math.max(rb.left + rb.width / 2 - pb.left + plot.scrollLeft, 96),
        Math.max(plot.scrollWidth - 96, 96),
      );
      setInstant(nextInstant);
      setTip({ index, series, x });
      setOpen(true);
      openRef.current = true;
      instantRef.current = true;
    },
    [],
  );

  const queue = useCallback(
    (index: number, series: "invoiced" | "collected" | null, el: Element) => {
      if (delayRef.current) {
        clearTimeout(delayRef.current);
        delayRef.current = null;
      }
      if (openRef.current || instantRef.current) {
        place(index, series, el, true);
        return;
      }
      delayRef.current = setTimeout(() => {
        place(index, series, el, false);
      }, 140);
    },
    [place],
  );

  const dismiss = useCallback(() => {
    if (delayRef.current) {
      clearTimeout(delayRef.current);
      delayRef.current = null;
    }
    openRef.current = false;
    setOpen(false);
    if (coolRef.current) clearTimeout(coolRef.current);
    coolRef.current = setTimeout(() => {
      instantRef.current = false;
    }, 400);
  }, []);

  useEffect(() => {
    return () => {
      if (delayRef.current) clearTimeout(delayRef.current);
      if (coolRef.current) clearTimeout(coolRef.current);
    };
  }, []);

  const active = open && tip ? tip.index : null;
  const row = tip ? rows[tip.index] : null;

  return (
    <div className="qb-chart">
      <div className="qb-legend">
        <span>
          <span className="qb-mark" style={{ background: "var(--zo-orange)" }} />
          <span>Invoiced</span>
        </span>
        <span>
          <span className="qb-mark" style={{ background: "var(--zo-teal)" }} />
          <span>Collected</span>
        </span>
      </div>
      <div
        ref={plotRef}
        className={`qb-plot${open ? " is-scrubbing" : ""}`}
        onPointerLeave={dismiss}
        onScroll={dismiss}
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="group"
          aria-label="Invoiced versus collected by month. Hover a bar for the amount."
          className="h-auto w-full min-w-[560px]"
        >
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <line
              key={f}
              x1={pad.l}
              x2={W - pad.r}
              y1={pad.t + innerH * (1 - f)}
              y2={pad.t + innerH * (1 - f)}
              stroke="var(--zo-border)"
              strokeWidth="1"
            />
          ))}
          {rows.map((r, i) => {
            const base = pad.l + slot * i + slot / 2;
            const ih = (r.invoiced / peak) * innerH;
            const ch = (r.collected / peak) * innerH;
            return (
              <g
                key={r.month}
                data-month={r.month}
                className={active === i ? "is-on" : undefined}
              >
                <rect
                  x={base - slot / 2 + 3}
                  y={pad.t - 2}
                  width={Math.max(slot - 6, 8)}
                  height={innerH + 4}
                  rx={8}
                  fill="var(--zo-text)"
                  opacity={active === i ? 0.045 : 0}
                  pointerEvents="none"
                />
                <rect
                  data-hit
                  x={base - slot / 2}
                  y={pad.t}
                  width={slot}
                  height={innerH + pad.b}
                  fill="transparent"
                  onPointerEnter={(e) => queue(i, null, e.currentTarget)}
                />
                <rect
                  data-bar
                  x={base - barW - 2}
                  y={pad.t + innerH - ih}
                  width={barW}
                  height={Math.max(ih, r.invoiced ? 2 : 0)}
                  rx={3}
                  fill="var(--zo-orange)"
                  onPointerEnter={(e) => queue(i, "invoiced", e.currentTarget)}
                />
                <rect
                  data-bar
                  x={base + 2}
                  y={pad.t + innerH - ch}
                  width={barW}
                  height={Math.max(ch, r.collected ? 2 : 0)}
                  rx={3}
                  fill="var(--zo-teal)"
                  onPointerEnter={(e) => queue(i, "collected", e.currentTarget)}
                />
                <text
                  x={base}
                  y={H - 6}
                  textAnchor="middle"
                  pointerEvents="none"
                  className="fill-[var(--zo-text-muted)]"
                  style={{ fontSize: 11, fontWeight: 500 }}
                >
                  {r.month}
                </text>
              </g>
            );
          })}
        </svg>
        <div
          role="tooltip"
          className="qb-tip"
          data-open={open ? "true" : "false"}
          data-instant={instant ? "true" : "false"}
          aria-hidden={open ? undefined : true}
          style={{ left: tip?.x ?? 0 }}
        >
          {row ? (
            <>
              <p className="qb-tip-title">{row.month}</p>
              <div className="qb-tip-row" data-on={tip?.series === "invoiced" ? "true" : "false"}>
                <span className="qb-mark" style={{ background: "var(--zo-orange)" }} />
                <span>Invoiced</span>
                <strong>{usd(row.invoiced)}</strong>
              </div>
              <div className="qb-tip-row" data-on={tip?.series === "collected" ? "true" : "false"}>
                <span className="qb-mark" style={{ background: "var(--zo-teal)" }} />
                <span>Collected</span>
                <strong>{usd(row.collected)}</strong>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/* ── section bodies ────────────────────────────────────────────────────── */

function ArBody({ ar }: { ar: NonNullable<QuickBooksOverview["ar"]> }) {
  const total = ar.total || 1;
  const aged = ar.buckets.filter((b) => b.amount > 0);
  return (
    <div className="qb-split">
      <Widget>
        <Kicker>By age</Kicker>
        <div className="qb-age-bar">
          {aged.map((b) => (
            <span
              key={b.label}
              title={`${b.label} — ${usd(b.amount)}`}
              style={{
                width: `${(b.amount / total) * 100}%`,
                background: AGE_TONE[b.label],
              }}
            />
          ))}
        </div>
        <div className="qb-age-legend">
          {aged.map((b) => (
            <div key={b.label} className="qb-age-item">
              <span aria-hidden className="qb-swatch" style={{ background: AGE_TONE[b.label] }} />
              <span className="qb-name">{b.label}</span>
              <span className="qb-amt">{usd(b.amount)}</span>
            </div>
          ))}
        </div>
      </Widget>
      <Widget>
        <Kicker>Who owes it</Kicker>
        <ShowMoreList
          items={ar.clients}
          initial={6}
          render={(c) => (
            <li key={c.client}>
              <span className="qb-name" title={c.client}>
                {c.client}
              </span>
              {c.oldest_days > 30 ? <span className="qb-pill">{c.oldest_days}d</span> : null}
              <span className="qb-amt">{usd(c.amount)}</span>
            </li>
          )}
        />
      </Widget>
    </div>
  );
}

function ClassPanel({ rc }: { rc: NonNullable<QuickBooksOverview["revenue_by_class"]> }) {
  if (!Array.isArray(rc.matrix) || rc.matrix.length === 0) {
    return <Empty what="No segment split yet." />;
  }
  const max = Math.max(...rc.matrix.map((c) => c.amount), 1);
  const cell = (parent: string, segment: string) =>
    rc.matrix.find((c) => c.parent === parent && c.segment === segment);

  return (
    <>
      <div className="qb-scroll">
        <table className="qb-matrix">
          <thead>
            <tr>
              <th scope="col" />
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
                <td>{p.replace(/ Revenue$/, "")}</td>
                {rc.segments.map((s) => {
                  const c = cell(p, s);
                  const amt = c?.amount ?? 0;
                  const heat = amt / max;
                  return (
                    <td
                      key={`${p}-${s}`}
                      style={{
                        background: `color-mix(in srgb, var(--zo-teal) ${Math.round(heat * 22)}%, var(--zo-surface))`,
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
        <p className="qb-note">
          {usd(rc.unclassified)} unclassified · {rc.coverage_pct}% of income has a segment.
        </p>
      ) : null}
    </>
  );
}

function ManagerPanel({
  am,
}: {
  am: NonNullable<QuickBooksOverview["by_account_manager"]>;
}) {
  const owners = am.managers.filter((m) => !m.is_overhead && m.income > 0);
  const overhead = am.managers.find((m) => m.is_overhead);
  const incomeTotal = am.managers.reduce((s, m) => s + m.income, 0);
  const overheadPct =
    overhead && incomeTotal ? Math.round((overhead.income / incomeTotal) * 1000) / 10 : 0;
  const max = Math.max(...owners.map((m) => m.income), 1);
  return (
    <div>
      <p className="qb-note" style={{ marginTop: 0, marginBottom: 8 }}>
        {owners.length} account managers
        {overheadPct > 0 ? ` · ${overheadPct}% income in Not Specified` : ""}
      </p>
      {owners.length === 0 ? (
        <Empty what="No account-manager split." />
      ) : (
        <ul className="qb-bars">
          {owners.map((m) => (
            <li key={m.manager}>
              <div className="qb-bar-head">
                <span className="qb-name">{m.manager}</span>
                <span>{usd(m.income)}</span>
              </div>
              <div className="qb-bar-track">
                <div className="qb-bar-fill" style={{ width: `${(m.income / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function lagKind(days: number, avg: number): "hot" | "warm" | "ok" {
  if (days >= Math.max(avg * 1.75, 40)) return "hot";
  if (days > avg) return "warm";
  return "ok";
}

function CollectionLagBody({
  days,
  sampleSize,
  clients,
}: {
  days: number;
  sampleSize: number;
  clients: { client: string; avg_days: number; amount: number }[];
}) {
  const maxDays = Math.max(...clients.map((c) => c.avg_days), 1);
  return (
    <>
      <div className="qb-dso-hero">
        <strong>{days}</strong>
        <span>days to get paid</span>
      </div>
      <p className="qb-note" style={{ marginTop: 0 }}>
        Average across {sampleSize.toLocaleString()} invoice payments this year
      </p>
      {clients.length ? (
        <>
          <p className="qb-list-label">Slowest to pay</p>
          <ShowMoreList
            items={clients}
            initial={5}
            head={
              <div className="qb-cols" aria-hidden>
                <span>Client</span>
                <span>Days</span>
                <span>Paid</span>
              </div>
            }
            render={(c) => {
              const kind = lagKind(c.avg_days, days);
              return (
                <li key={c.client} className="qb-lag">
                  <span className="qb-name" title={c.client}>
                    {c.client}
                  </span>
                  <span className={`qb-days qb-lag-${kind}`}>{c.avg_days}d</span>
                  <span className="qb-amt">{compact(c.amount)}</span>
                  <span className="qb-share" aria-hidden>
                    <span
                      className={`qb-lag-${kind}`}
                      style={{ width: `${Math.max((c.avg_days / maxDays) * 100, 4)}%` }}
                    />
                  </span>
                </li>
              );
            }}
          />
        </>
      ) : null}
    </>
  );
}

function TopPayersBody({
  payers,
}: {
  payers: { customer: string; amount: number }[];
}) {
  const max = Math.max(...payers.map((p) => p.amount), 1);
  return (
    <ShowMoreList
      items={payers}
      initial={6}
      head={
        <div className="qb-cols qb-cols-2" aria-hidden>
          <span>Client</span>
          <span>Paid</span>
        </div>
      }
      render={(p) => (
        <li key={p.customer} className="qb-rank">
          <span className="qb-name" title={p.customer}>
            {p.customer}
          </span>
          <span className="qb-amt">{usd(p.amount)}</span>
          <span className="qb-share" aria-hidden>
            <span style={{ width: `${Math.max((p.amount / max) * 100, 4)}%` }} />
          </span>
        </li>
      )}
    />
  );
}

/* ── container ─────────────────────────────────────────────────────────── */

export function QuickBooksPanels() {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [data, setData] = useState<QuickBooksOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState("health");
  const rootRef = useRef<HTMLDivElement>(null);

  const load = async (y = year) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ year: String(y) });
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/overview?${params.toString()}`,
      );
      if (!res.ok) throw new Error(`QuickBooks returned ${res.status}`);
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach QuickBooks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(year);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year]);

  useEffect(() => {
    const nodes = SECTIONS.map((s) => document.getElementById(s.id)).filter(
      Boolean,
    ) as HTMLElement[];
    if (!nodes.length) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]?.target?.id) setActiveSection(visible[0].target.id);
      },
      { rootMargin: "-20% 0px -55% 0px", threshold: [0.1, 0.4, 0.7] },
    );
    nodes.forEach((n) => obs.observe(n));
    return () => obs.disconnect();
  }, [data]);

  const net = useMemo(
    () => (data?.ar?.total ?? 0) - (data?.ap?.total ?? 0),
    [data?.ar?.total, data?.ap?.total],
  );

  if (loading && !data) {
    return (
      <div className="qb-ledger">
        <LedgerSkeleton />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="qb-ledger">
        <div className="qb-error">
          <p>{error ?? "No QuickBooks data"}</p>
          <button type="button" onClick={() => void load()} className="qb-icon-btn">
            Retry
          </button>
        </div>
      </div>
    );
  }

  const trend = data.monthly_trend;
  const err = data.errors ?? {};
  const years = [currentYear, currentYear - 1, currentYear - 2];
  const classifiedPct =
    data.class_coverage?.coverage_pct ?? data.revenue_by_class?.coverage_pct;

  return (
    <FadeIn>
      <div ref={rootRef} className="qb-ledger">
        <div className="qb-toolbar">
          <div className="qb-live">
            <span aria-hidden className="qb-live-dot" />
            {data.sync_status === "failed" ? "Sync failed" : "Synced"}
            {data.synced_at ? (
              <span className="qb-company">
                {new Date(data.synced_at).toLocaleString()}
              </span>
            ) : null}
            {data.company ? (
              <span className="qb-company">{data.company.legal_name}</span>
            ) : null}
          </div>
          <div className="qb-tools">
            <YearSeg year={year} years={years} onChange={setYear} />
          </div>
        </div>

        <SectionNav active={activeSection} />

        <Section id="health" title="Where we stand">
          <HealthStrip
            ar={data.ar?.total ?? 0}
            ap={data.ap?.total ?? 0}
            net={net}
            booked={trend?.total ?? 0}
            year={data.year}
            lastBooked={trend?.last_booked_month}
            liquidity={data.liquidity}
          />
          {err.ar || err.ap || err.liquidity ? (
            <p className="qb-fail">Some health metrics did not load.</p>
          ) : null}
        </Section>

        <Section id="cash" title="Are we collecting?" hint={`${data.year}`}>
          {data.billing_vs_cash || data.cash_collections || data.dso ? (
            <div className="qb-stack">
              {data.billing_vs_cash ? (
                <Widget padded={false}>
                  <div className="qb-cash-grid">
                    <div className="qb-cash-cell">
                      <span className="qb-metric-label">Invoiced</span>
                      <strong>{usd(data.billing_vs_cash.invoiced_total)}</strong>
                    </div>
                    <div className="qb-cash-cell">
                      <span className="qb-metric-label">Collected</span>
                      <strong className="qb-tone-in">
                        {usd(data.billing_vs_cash.collected_total)}
                      </strong>
                    </div>
                    <div className="qb-cash-cell">
                      <span className="qb-metric-label">Still open</span>
                      <strong className="qb-tone-out">
                        {usd(data.billing_vs_cash.open_ar)}
                      </strong>
                    </div>
                    <div className="qb-cash-cell">
                      <span className="qb-metric-label">Collection rate</span>
                      <strong>{data.billing_vs_cash.collection_rate_pct}%</strong>
                    </div>
                  </div>
                  <div className="qb-chart-pad">
                    <DualMonthChart rows={data.billing_vs_cash.by_month} />
                  </div>
                </Widget>
              ) : err.billing_vs_cash ? (
                <Widget>
                  <Empty what="Billing vs cash unavailable." />
                </Widget>
              ) : null}

              <div className="qb-split">
                <Widget>
                  <div className="qb-kicker-row">
                    <div className="qb-kicker">
                      Collection lag
                      <InfoTip label="What collection lag means">
                        Average days between sending an invoice and receiving the
                        payment that closed it. Built from this year’s payments that
                        QuickBooks linked back to an invoice. Lower means customers
                        are paying faster.
                      </InfoTip>
                    </div>
                  </div>
                  {data.dso ? (
                    data.dso.dso_days != null ? (
                      <CollectionLagBody
                        days={data.dso.dso_days}
                        sampleSize={data.dso.sample_size}
                        clients={data.dso.slowest_clients}
                      />
                    ) : (
                      <Empty what="Not enough payments are linked to invoices to measure this." />
                    )
                  ) : (
                    <Empty what="Collection lag unavailable." />
                  )}
                </Widget>
                <Widget>
                  <Kicker>Top payers</Kicker>
                  {data.cash_collections?.top_payers?.length ? (
                    <TopPayersBody payers={data.cash_collections.top_payers} />
                  ) : (
                    <Empty what="No payments recorded this year." />
                  )}
                </Widget>
              </div>
            </div>
          ) : (
            <Widget>
              <Empty what="Cash insights unavailable — check the QuickBooks connection." />
            </Widget>
          )}
        </Section>

        <Section id="receivables" title="Who owes us">
          {data.ar ? (
            <ArBody ar={data.ar} />
          ) : (
            <Widget>
              <Empty what="No open receivables." />
            </Widget>
          )}
        </Section>

        <Section id="payables" title="What we owe">
          {data.ap ? (
            <div className="qb-split">
              <Widget>
                <Kicker>By age</Kicker>
                <ul className="qb-group">
                  {data.ap.buckets
                    .filter((b) => b.amount > 0)
                    .map((b) => (
                      <li key={b.label}>
                        <span
                          aria-hidden
                          className="qb-swatch"
                          style={{ background: AGE_TONE[b.label] }}
                        />
                        <span className="qb-name">{b.label}</span>
                        <span className="qb-amt">{usd(b.amount)}</span>
                      </li>
                    ))}
                </ul>
              </Widget>
              <Widget>
                <Kicker>Vendors</Kicker>
                <ShowMoreList
                  items={data.ap.vendors}
                  initial={7}
                  render={(v) => (
                    <li key={v.vendor}>
                      <span className="qb-name">{v.vendor}</span>
                      <span className="qb-amt">{usd(v.amount)}</span>
                    </li>
                  )}
                />
              </Widget>
            </div>
          ) : (
            <Widget>
              <Empty what="No open payables." />
            </Widget>
          )}
        </Section>

        <Section id="revenue" title="How revenue is composed" hint={`${data.year}`}>
          <div className="qb-stack">
            <Widget>
              <Kicker>Booked by month</Kicker>
              {trend ? (
                <TrendChart months={trend.months} peak={trend.peak} />
              ) : (
                <Empty what="Monthly trend unavailable." />
              )}
            </Widget>
            <div className="qb-split">
              <Widget>
                <div className="qb-kicker-row">
                  <Kicker>By segment</Kicker>
                  {classifiedPct != null ? (
                    <span className="qb-hint">{classifiedPct}% classified</span>
                  ) : null}
                </div>
                {data.revenue_by_class ? (
                  <ClassPanel rc={data.revenue_by_class} />
                ) : (
                  <Empty what="Segment split unavailable." />
                )}
              </Widget>
              <Widget>
                <Kicker>By account manager</Kicker>
                {data.by_account_manager ? (
                  <ManagerPanel am={data.by_account_manager} />
                ) : (
                  <Empty what="Account manager split unavailable." />
                )}
              </Widget>
            </div>
          </div>
        </Section>

        <Section id="clients" title="Which clients carry the book">
          <div className="qb-split">
            <Widget>
              <Kicker>Profitability</Kicker>
              {data.client_profitability ? (
                <>
                  <div className="qb-scroll">
                    <table className="qb-table">
                      <thead>
                        <tr>
                          <th>Client</th>
                          <th>Income</th>
                          <th>Cost</th>
                          <th>Net</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.client_profitability.clients.slice(0, 9).map((c) => (
                          <tr key={c.client}>
                            <td title={c.client}>{c.client}</td>
                            <td>{compact(c.income)}</td>
                            <td>{c.expense ? compact(c.expense) : "—"}</td>
                            <td>{compact(c.net)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="qb-note">
                    Only {usd(data.client_profitability.attributed_expense)} of cost is
                    tagged to a client, so margins read high.
                  </p>
                </>
              ) : (
                <Empty what="Client profitability unavailable." />
              )}
            </Widget>
            <div className="qb-stack">
              <Widget>
                <Kicker>Sales by customer</Kicker>
                {data.sales_by_customer?.clients?.length ? (
                  <ShowMoreList
                    items={data.sales_by_customer.clients}
                    initial={8}
                    render={(c) => (
                      <li key={c.client}>
                        <span className="qb-name">{c.client}</span>
                        <span className="qb-amt">{compact(c.amount)}</span>
                      </li>
                    )}
                  />
                ) : (
                  <Empty what="Sales by customer unavailable." />
                )}
              </Widget>
              <Widget>
                <Kicker>Credits & adjustments</Kicker>
                {data.credit_memos ? (
                  data.credit_memos.count > 0 ? (
                    <>
                      <p className="qb-hero-line">
                        <strong>{usd(data.credit_memos.total)}</strong>
                        <span>across {data.credit_memos.count} memos</span>
                      </p>
                      <ShowMoreList
                        items={data.credit_memos.clients}
                        initial={5}
                        render={(c) => (
                          <li key={c.client}>
                            <span className="qb-name">{c.client}</span>
                            <span className="qb-amt">{compact(c.amount)}</span>
                          </li>
                        )}
                      />
                    </>
                  ) : (
                    <Empty what="No credit memos this year." />
                  )
                ) : (
                  <Empty what="Credit memos unavailable." />
                )}
                {data.customers ? (
                  <p className="qb-note">
                    {data.customers.count} active customers in QuickBooks (join keys
                    ready for Teamwork).
                  </p>
                ) : null}
              </Widget>
            </div>
          </div>
        </Section>

        <Section id="costs" title="Unattributed & committed spend">
          <div className="qb-stack">
            {data.unattached_cost ? (
              <Widget>
                <Kicker>Cost with no client</Kicker>
                <p className="qb-unattached">
                  {usd(data.unattached_cost.cost_of_service_unattached)}
                </p>
                <p className="qb-note">
                  Cost of services with no client ·{" "}
                  {data.unattached_cost.unattached_count.toLocaleString()} of{" "}
                  {data.unattached_cost.purchase_count.toLocaleString()} purchases (
                  {data.unattached_cost.unattached_pct}%)
                </p>
                <ShowMoreList
                  items={data.unattached_cost.accounts}
                  initial={6}
                  render={(a) => (
                    <li key={a.account}>
                      <span className="qb-name">{a.account}</span>
                      {a.is_cost_of_service ? (
                        <span className="qb-pill">billable</span>
                      ) : null}
                      <span className="qb-amt">{compact(a.amount)}</span>
                    </li>
                  )}
                />
              </Widget>
            ) : (
              <Widget>
                <Empty what="Unattached cost unavailable." />
              </Widget>
            )}

            <div className="qb-split">
              <Widget>
                <Kicker>Open purchase orders</Kicker>
                {data.purchase_orders ? (
                  <>
                    <p className="qb-hero-line">
                      <strong>{usd(data.purchase_orders.open_total)}</strong>
                      <span>open · {data.purchase_orders.open_count} POs</span>
                    </p>
                    <ShowMoreList
                      items={data.purchase_orders.vendors}
                      initial={6}
                      render={(v) => (
                        <li key={v.vendor}>
                          <span className="qb-name">{v.vendor}</span>
                          <span className="qb-amt">{compact(v.amount)}</span>
                        </li>
                      )}
                    />
                  </>
                ) : (
                  <Empty what="No purchase orders (or unavailable)." />
                )}
              </Widget>
              <Widget>
                <Kicker>Spend by vendor</Kicker>
                {data.expenses_by_vendor ? (
                  <>
                    <p className="qb-note" style={{ marginTop: 0 }}>
                      Top 3 = {data.expenses_by_vendor.top3_concentration_pct}% of{" "}
                      {usd(data.expenses_by_vendor.total)}
                    </p>
                    <ShowMoreList
                      items={data.expenses_by_vendor.vendors}
                      initial={8}
                      render={(v) => (
                        <li key={v.vendor}>
                          <span className="qb-name">{v.vendor}</span>
                          <span className="qb-amt">{compact(v.amount)}</span>
                        </li>
                      )}
                    />
                  </>
                ) : (
                  <Empty what="Vendor expenses unavailable." />
                )}
              </Widget>
            </div>

            {data.bill_payments ? (
              <Widget>
                <Kicker>Bills paid</Kicker>
                <p className="qb-hero-line" style={{ marginBottom: 0 }}>
                  <strong>{usd(data.bill_payments.total_paid)}</strong>
                  <span>{data.bill_payments.payment_count} payments</span>
                </p>
              </Widget>
            ) : null}
          </div>
        </Section>

        <Section id="activity" title="Recent ledger changes">
          {data.activity ? (
            <Widget>
              <div className="qb-activity">
                <span className="qb-chip">
                  <strong>{data.activity.total}</strong>
                  <span>changes</span>
                </span>
                {data.activity.entities.map((e) => (
                  <span key={e.entity} className="qb-chip">
                    <strong>{e.changed}</strong>
                    <span>{e.entity}</span>
                  </span>
                ))}
              </div>
            </Widget>
          ) : (
            <Widget>
              <Empty what="Activity feed unavailable." />
            </Widget>
          )}
        </Section>

        {Object.keys(err).length > 0 ? (
          <p className="qb-fail">
            Some panels did not load: {Object.keys(err).join(", ")}.
          </p>
        ) : null}
      </div>
    </FadeIn>
  );
}
