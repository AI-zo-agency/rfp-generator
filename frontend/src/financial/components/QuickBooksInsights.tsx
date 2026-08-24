"use client";

import { useCallback, useEffect, useState } from "react";
import { RotateCcw, Sparkles } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

interface ChaseRow {
  id: string;
  client: string;
  /** The past-due portion only — not the whole balance. */
  overdue_figure: string;
  /** Age of the oldest overdue invoice. */
  overdue_days: number;
  /** Dollar-weighted mean age. This is the one that pairs truthfully with the
   *  amount: OCF's invoices run 24–73 days, so "$11,966, 73d late" is false. */
  avg_overdue_days: number;
  balance_figure: string;
  invoice_count: number;
  slow_payer: boolean;
}

interface MarginRow {
  id: string;
  label: string;
  figure: string;
  detail: string;
  kind: string;
}

interface HygieneRow {
  id: string;
  label: string;
  amount: number;
  figure: string;
  kind: string;
}

interface PositionData {
  cash_figure: string;
  overdue_ap_figure: string;
  overdue_ar_figure: string;
  net_figure: string;
  net_amount: number;
}

interface InsightsData {
  status: "ok" | "empty";
  brief: string;
  notes: Record<string, string>;
  /** Null when there is no cash figure to anchor the strip. */
  position: PositionData | null;
  chase: ChaseRow[];
  margin: MarginRow[];
  hygiene: HygieneRow[];
  as_of: string | null;
  generated_at: string | null;
  provider: string | null;
  stale: boolean;
  /** Only present on the regenerate response: "ok" | "failed". */
  generated?: string;
}

export function QuickBooksInsights({ year }: { year: number }) {
  const [data, setData] = useState<InsightsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/ai-insights?year=${year}`,
      );
      if (!res.ok) throw new Error(`${res.status}`);
      setData(await res.json());
      setError(null);
    } catch {
      setError("Couldn't load insights.");
    }
  }, [year]);

  useEffect(() => {
    void load();
  }, [load]);

  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/ai-insights/regenerate?year=${year}`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const next: InsightsData = await res.json();
      setData(next);
      if (next.generated === "failed") {
        setError("Couldn't generate a new brief. The figures below are current.");
      }
    } catch {
      setError("Couldn't generate a new brief. The figures below are current.");
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return error ? <p className="qb-insights-error" role="status">{error}</p> : null;
  }

  return (
    <section className="qb-insights" aria-busy={busy || undefined} aria-live="polite">
      <header className="qb-insights-head">
        <h3>
          <Sparkles size={14} strokeWidth={2.25} aria-hidden /> This week
        </h3>
        <div className="qb-insights-meta">
          {data.as_of ? (
            <span data-stale={data.stale || undefined}>
              {data.stale ? `As of ${data.as_of}` : `Today, ${data.as_of}`}
            </span>
          ) : null}
          <button type="button" onClick={regenerate} disabled={busy}>
            <RotateCcw size={13} strokeWidth={2.25} aria-hidden />
            {busy ? "Working…" : "Regenerate"}
          </button>
        </div>
      </header>

      {error ? <p className="qb-insights-error">{error}</p> : null}

      {data.position ? (
        <dl className="qb-insights-position">
          <div>
            <dt>Cash</dt>
            <dd>{data.position.cash_figure}</dd>
          </div>
          <div>
            <dt>Bills overdue</dt>
            <dd>{data.position.overdue_ap_figure}</dd>
          </div>
          <div>
            <dt>Invoices overdue</dt>
            <dd>{data.position.overdue_ar_figure}</dd>
          </div>
          <div data-net data-short={data.position.net_amount < 0 || undefined}>
            <dt>Net</dt>
            <dd>{data.position.net_figure}</dd>
          </div>
        </dl>
      ) : null}

      {data.brief ? (
        <p className="qb-insights-brief">{data.brief}</p>
      ) : (
        <p className="qb-insights-brief qb-insights-empty">
          No brief yet. The nightly sync writes one, or generate it now.
        </p>
      )}

      {data.chase.length ? (
        <div className="qb-insights-list">
          <h4>Get cash in</h4>
          <ul>
            {data.chase.map((row) => (
              <li key={row.id}>
                <div>
                  <p className="qb-insights-row-head">
                    {row.client}
                    {row.slow_payer ? (
                      <span className="qb-insights-tag">slow payer</span>
                    ) : null}
                  </p>
                  <p className="qb-insights-row-sub">
                    {row.avg_overdue_days}d overdue
                    {/* One invoice cannot have a spread, so avg == oldest. */}
                    {row.overdue_days !== row.avg_overdue_days
                      ? ` (oldest ${row.overdue_days}d)`
                      : ""}
                    {" · "}
                    {row.invoice_count}{" "}
                    {row.invoice_count === 1 ? "invoice" : "invoices"}
                    {/* Only worth saying when part of the balance isn't due yet. */}
                    {row.balance_figure !== row.overdue_figure
                      ? ` · ${row.balance_figure} owed`
                      : ""}
                    {data.notes[row.id] ? ` · ${data.notes[row.id]}` : ""}
                  </p>
                </div>
                <span className="qb-insights-figure">{row.overdue_figure}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.margin.length ? (
        <div className="qb-insights-list">
          <h4>Watch margin</h4>
          <ul>
            {data.margin.map((row) => (
              <li key={row.id}>
                <div>
                  <p className="qb-insights-row-head">{row.label}</p>
                  <p className="qb-insights-row-sub">
                    {row.detail}
                    {data.notes[row.id] ? ` ${data.notes[row.id]}` : ""}
                  </p>
                </div>
                <span className="qb-insights-figure">{row.figure}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.hygiene.length ? (
        <div className="qb-insights-list">
          <h4>Fix in QuickBooks</h4>
          <ul>
            {data.hygiene.map((row) => (
              <li key={row.id}>
                <div>
                  <p className="qb-insights-row-head">{row.label}</p>
                  {data.notes[row.id] ? (
                    <p className="qb-insights-row-sub">{data.notes[row.id]}</p>
                  ) : null}
                </div>
                <span className="qb-insights-figure">{row.figure}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
