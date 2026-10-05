"use client";

/**
 * Top-level Financial Forecast tab — QB + HubSpot hybrid outlook.
 * Reuses the ledger forecast view; lives here so it is not buried under QuickBooks.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ForecastView } from "./QuickBooksPanels";
import type { QuickBooksOverview } from "../types/quickbooks";
import "./QuickBooksLedger.css";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

function isAbortError(err: unknown) {
  return err instanceof DOMException && err.name === "AbortError";
}

export function ForecastPanels() {
  const currentYear = new Date().getFullYear();
  const years = [currentYear, currentYear - 1, currentYear - 2];
  const [year, setYear] = useState(currentYear);
  const [data, setData] = useState<QuickBooksOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async (y: number, opts?: { soft?: boolean }) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    if (!opts?.soft) setData(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/overview?year=${y}`,
        { signal: ac.signal },
      );
      if (!res.ok) throw new Error(`Forecast returned ${res.status}`);
      const payload = (await res.json()) as QuickBooksOverview;
      if (ac.signal.aborted) return;
      setData(payload);
    } catch (err) {
      if (isAbortError(err) || ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Could not load forecast");
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    if (syncing) return;
    setSyncing(true);
    setError(null);
    try {
      const { trackClick } = await import("@/lib/zo-analytics");
      trackClick("financial.forecast_refresh", {
        path: "/financial-insights",
        tab: "forecast",
        funnel: true,
      });
      const res = await fetch(`${API_BASE}/api/v1/financials/quickbooks/refresh`, {
        method: "POST",
      });
      if (res.status === 409) {
        throw new Error("A sync is already running — try again in a minute.");
      }
      if (!res.ok) throw new Error(`Sync returned ${res.status}`);
      await load(year, { soft: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not refresh forecast");
    } finally {
      setSyncing(false);
    }
  }, [load, syncing, year]);

  useEffect(() => {
    void load(year);
    return () => abortRef.current?.abort();
  }, [load, year]);

  const busy = loading || syncing;
  let syncLabel = "Forecast ready";
  if (syncing) syncLabel = "Refreshing…";
  else if (loading) syncLabel = `Loading ${year}…`;

  return (
    <TooltipProvider delayDuration={120}>
      <div className="qb-ledger" aria-busy={busy || undefined}>
        <div className="qb-toolbar" data-fin="chrome">
          <p className="qb-sync">
            <span className="qb-sync-dot" data-busy={busy ? "true" : undefined} aria-hidden />
            {syncLabel}
            {!busy && data?.synced_at ? (
              <span className="qb-sync-meta">{new Date(data.synced_at).toLocaleString()}</span>
            ) : null}
            {!busy && data?.company ? (
              <span className="qb-sync-meta">{data.company.legal_name}</span>
            ) : null}
          </p>
          <div className="qb-toolbar-actions">
            <button
              type="button"
              className="qb-retry"
              onClick={() => void refresh()}
              disabled={busy}
              data-active={syncing ? "true" : undefined}
            >
              <RefreshCw
                className={syncing ? "animate-spin" : undefined}
                size={13}
                strokeWidth={2.25}
                aria-hidden
              />
              Refresh
            </button>
            <ToggleGroup
              type="single"
              value={String(year)}
              onValueChange={(v) => v && setYear(Number(v))}
              className="qb-years"
              aria-label="Fiscal year"
              aria-busy={busy || undefined}
            >
              {years.map((y) => (
                <ToggleGroupItem key={y} value={String(y)} aria-label={String(y)}>
                  {y}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </div>

        {error ? (
          <p className="qb-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="qb-view">
          {data ? <ForecastView data={data} /> : null}
        </div>
      </div>
    </TooltipProvider>
  );
}
