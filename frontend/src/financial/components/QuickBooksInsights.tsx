"use client";

import { useCallback, useEffect, useState } from "react";
import { RotateCcw, Sparkles } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

interface ChaseRow {
  id: string;
  client: string;
  amount: number;
  figure: string;
  oldest_days: number;
  invoices: number;
  slow_payer: boolean;
  dollar_days: number;
}

interface HygieneRow {
  id: string;
  label: string;
  amount: number;
  figure: string;
  kind: string;
}

interface InsightsData {
  status: "ok" | "empty";
  brief: string;
  notes: Record<string, string>;
  chase: ChaseRow[];
  hygiene: HygieneRow[];
  as_of: string | null;
  generated_at: string | null;
  provider: string | null;
  stale: boolean;
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
      setData(await res.json());
    } catch {
      setError("Couldn't generate a new brief. The figures below are current.");
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return error ? <p className="qb-insights-error">{error}</p> : null;
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

      {data.brief ? (
        <p className="qb-insights-brief">{data.brief}</p>
      ) : (
        <p className="qb-insights-brief qb-insights-empty">
          No brief yet. The nightly sync writes one, or generate it now.
        </p>
      )}

      {data.chase.length ? (
        <div className="qb-insights-list">
          <h4>Call this week</h4>
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
                    {row.oldest_days}d late ·{" "}
                    {row.invoices} {row.invoices === 1 ? "invoice" : "invoices"}
                    {data.notes[row.id] ? ` · ${data.notes[row.id]}` : ""}
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
