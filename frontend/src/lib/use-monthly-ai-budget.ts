"use client";

import { useEffect, useState } from "react";

/**
 * Shared monthly AI budget snapshot for the sidebar meter.
 * One in-flight fetch + one poll timer for the whole app.
 */

export type MonthlyAiBudgetSnapshot = {
  enabled: boolean;
  limitUsd: number;
  spentUsd: number;
  remainingUsd: number;
  blocked: boolean;
  proposalSpentUsd: number;
  financialSpentUsd: number;
  outreachSpentUsd: number;
  weekSpentUsd: number;
  weekProposalSpentUsd: number;
  weekFinancialSpentUsd: number;
  weekOutreachSpentUsd: number;
  dayLimitUsd: number;
  daySpentUsd: number;
  dayProposalSpentUsd: number;
  dayFinancialSpentUsd: number;
  dayOutreachSpentUsd: number;
};

const POLL_MS = 120_000;
const FOCUS_MIN_GAP_MS = 45_000;

let cached: MonthlyAiBudgetSnapshot | null | undefined = undefined;
let inflight: Promise<MonthlyAiBudgetSnapshot | null> | null = null;
let lastFetchAt = 0;
let pollTimer: number | null = null;
let focusBound = false;
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

function parseBudget(data: Record<string, unknown>): MonthlyAiBudgetSnapshot | null {
  if (!data || data.enabled === false) return null;
  return {
    enabled: Boolean(data.enabled),
    limitUsd: Number(data.limit_usd ?? 0),
    spentUsd: Number(data.spent_usd ?? 0),
    remainingUsd: Number(data.remaining_usd ?? 0),
    blocked: Boolean(data.blocked),
    proposalSpentUsd: Number(data.proposal_spent_usd ?? 0),
    financialSpentUsd: Number(data.financial_spent_usd ?? 0),
    outreachSpentUsd: Number(data.outreach_spent_usd ?? 0),
    weekSpentUsd: Number(data.week_spent_usd ?? 0),
    weekProposalSpentUsd: Number(data.week_proposal_spent_usd ?? 0),
    weekFinancialSpentUsd: Number(data.week_financial_spent_usd ?? 0),
    weekOutreachSpentUsd: Number(data.week_outreach_spent_usd ?? 0),
    dayLimitUsd: Number(data.day_limit_usd ?? 0),
    daySpentUsd: Number(data.day_spent_usd ?? 0),
    dayProposalSpentUsd: Number(data.day_proposal_spent_usd ?? 0),
    dayFinancialSpentUsd: Number(data.day_financial_spent_usd ?? 0),
    dayOutreachSpentUsd: Number(data.day_outreach_spent_usd ?? 0),
  };
}

async function fetchBudget(): Promise<MonthlyAiBudgetSnapshot | null> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await fetch("/api/llm-cost/monthly-budget", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) return cached === undefined ? null : (cached ?? null);
      const data = (await res.json()) as Record<string, unknown>;
      const next = parseBudget(data);
      cached = next;
      lastFetchAt = Date.now();
      notify();
      return next;
    } catch {
      return cached === undefined ? null : (cached ?? null);
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

function ensurePolling() {
  if (typeof window === "undefined") return;
  if (pollTimer == null) {
    pollTimer = window.setInterval(() => {
      void fetchBudget();
    }, POLL_MS);
  }
  if (!focusBound) {
    focusBound = true;
    const onFocus = () => {
      if (Date.now() - lastFetchAt < FOCUS_MIN_GAP_MS) return;
      void fetchBudget();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") onFocus();
    });
  }
}

/**
 * Subscribe to the shared monthly AI budget. First subscriber triggers load;
 * Sidebar shares one network request.
 */
export function useMonthlyAiBudget(): MonthlyAiBudgetSnapshot | null {
  const [budget, setBudget] = useState<MonthlyAiBudgetSnapshot | null>(
    () => (cached === undefined ? null : cached),
  );

  useEffect(() => {
    const sync = () => {
      setBudget(cached === undefined ? null : cached);
    };
    listeners.add(sync);
    ensurePolling();
    if (cached === undefined || Date.now() - lastFetchAt > POLL_MS) {
      void fetchBudget();
    } else {
      sync();
    }
    return () => {
      listeners.delete(sync);
    };
  }, []);

  return budget;
}

export type WeekCostHistoryRow = {
  label: string;
  spentUsd: number;
  proposalSpentUsd: number;
  financialSpentUsd: number;
  outreachSpentUsd: number;
};

let historyCache: WeekCostHistoryRow[] | null = null;
let historyInflight: Promise<WeekCostHistoryRow[]> | null = null;

function mapHistory(raw: unknown): WeekCostHistoryRow[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((row) => {
    const item = (row ?? {}) as Record<string, unknown>;
    return {
      label: String(item.label ?? ""),
      spentUsd: Number(item.spent_usd ?? 0),
      proposalSpentUsd: Number(item.proposal_spent_usd ?? 0),
      financialSpentUsd: Number(item.financial_spent_usd ?? 0),
      outreachSpentUsd: Number(item.outreach_spent_usd ?? 0),
    };
  });
}

/** Prior weeks, loaded once when History is opened. Shared across dashboards. */
export function useWeekCostHistory(enabled: boolean): {
  weeks: WeekCostHistoryRow[] | null;
  loading: boolean;
  error: string | null;
} {
  const [weeks, setWeeks] = useState<WeekCostHistoryRow[] | null>(historyCache);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || historyCache) {
      if (historyCache) setWeeks(historyCache);
      return;
    }
    let cancelled = false;
    if (!historyInflight) {
      historyInflight = (async () => {
        const res = await fetch("/api/llm-cost/weekly-history", {
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (!res.ok) throw new Error(`Weekly history failed (${res.status})`);
        const data = (await res.json()) as { weeks?: unknown };
        historyCache = mapHistory(data.weeks);
        return historyCache;
      })().catch((err: unknown) => {
        historyInflight = null;
        throw err;
      });
    }
    historyInflight
      .then((rows) => {
        if (!cancelled) setWeeks(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.warn("[llm-cost] weekly history unavailable:", err);
        setError(err instanceof Error ? err.message : "History unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return {
    weeks,
    loading: enabled && weeks === null && error === null,
    error,
  };
}
