"use client";

import { useEffect, useState } from "react";

/**
 * Shared monthly AI budget snapshot for header badge + sidebar.
 * One in-flight fetch + one poll timer for the whole app (avoids double
 * /monthly-budget spam every 30s from TopBar + Sidebar).
 */

export type MonthlyAiBudgetSnapshot = {
  enabled: boolean;
  limitUsd: number;
  spentUsd: number;
  remainingUsd: number;
  blocked: boolean;
  proposalSpentUsd: number;
  financialSpentUsd: number;
  weekSpentUsd: number;
  weekProposalSpentUsd: number;
  weekFinancialSpentUsd: number;
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
    weekSpentUsd: Number(data.week_spent_usd ?? 0),
    weekProposalSpentUsd: Number(data.week_proposal_spent_usd ?? 0),
    weekFinancialSpentUsd: Number(data.week_financial_spent_usd ?? 0),
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
 * TopBar + Sidebar share one network request.
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
