"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { AiInsightsData } from "../components/AiInsightsPanel";
import type { AuditItem } from "../components/AuditQueueTable";
import type { PeriodGranularity, PeriodInsights } from "../types/iworker";
import type { Signal } from "../types/quickbooks";
import {
  buildNoteCards,
  countByBadge,
  type InsightsData,
  type NoteCard,
  type QbInsights,
} from "./use-qb-insights";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

function periodSignalSeverity(severity: string): Signal["severity"] {
  if (severity === "scope") return "critical";
  if (severity === "cost") return "warn";
  return "info";
}

function auditSeverity(severity: string): Signal["severity"] {
  if (severity === "HIGH") return "critical";
  if (severity === "MEDIUM") return "warn";
  return "info";
}

function buildIworkerSignals(
  periodInsights: PeriodInsights | undefined,
  auditItems: AuditItem[],
): Signal[] {
  const seen = new Set<string>();
  const out: Signal[] = [];

  for (const signal of periodInsights?.signals ?? []) {
    if (seen.has(signal.id)) continue;
    seen.add(signal.id);
    out.push({
      id: signal.id,
      severity: periodSignalSeverity(signal.severity),
      headline: signal.headline,
      detail: signal.detail,
    });
  }

  for (const item of auditItems) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    const figure =
      item.amount > 0
        ? `$${item.amount.toFixed(0)}`
        : item.hours > 0
          ? `${item.hours}h`
          : undefined;
    out.push({
      id: item.id,
      severity: auditSeverity(item.severity),
      headline: item.type,
      detail: item.reason,
      figure,
    });
  }

  return out;
}

function aiToInsightsData(
  ai: AiInsightsData | null,
  periodLabel?: string,
): InsightsData | null {
  if (!ai || ai.status === "empty") return null;
  const brief = ai.summary?.leadership_brief_text?.trim();
  if (!brief) return null;
  return {
    status: "ok",
    brief,
    notes: {},
    as_of: ai.generated_at,
    generated_at: ai.generated_at,
    provider: ai.provider ?? null,
    model: ai.model ?? null,
    stale: false,
    period_label: ai.stats?.period_label ?? periodLabel ?? null,
  };
}

function isAiInsightsPayload(raw: unknown): raw is AiInsightsData {
  if (!raw || typeof raw !== "object") return false;
  const row = raw as AiInsightsData;
  return typeof row.status === "string" && Boolean(row.summary);
}

function buildSummaryCards(ai: AiInsightsData | null): NoteCard[] {
  if (!ai?.summary || ai.status === "empty") return [];
  const cards: NoteCard[] = [];

  ai.summary.top_3_risks?.forEach((detail, index) => {
    const text = detail?.trim();
    if (!text) return;
    cards.push({
      id: `iworker:ai:risk:${index}`,
      headline: "Risk",
      detail: text,
      badge: "Risk",
      aiEnhanced: true,
    });
  });

  ai.summary.top_3_wins?.forEach((detail, index) => {
    const text = detail?.trim();
    if (!text) return;
    cards.push({
      id: `iworker:ai:win:${index}`,
      headline: "Win",
      detail: text,
      badge: "Opportunity",
      aiEnhanced: true,
    });
  });

  ai.summary.margin_recommendations?.forEach((detail, index) => {
    const text = detail?.trim();
    if (!text) return;
    cards.push({
      id: `iworker:ai:rec:${index}`,
      headline: "Recommendation",
      detail: text,
      badge: "Action",
      aiEnhanced: true,
    });
  });

  return cards;
}

export function useIworkerInsights(
  periodInsights: PeriodInsights | undefined,
  auditItems: AuditItem[],
  granularity: PeriodGranularity,
  periodStart: string | null,
): QbInsights {
  const [aiInsights, setAiInsights] = useState<AiInsightsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const query = useMemo(() => {
    const params = new URLSearchParams({ granularity });
    if (periodStart) params.set("period_start", periodStart);
    return params.toString();
  }, [granularity, periodStart]);

  const load = useCallback(async () => {
    if (!periodInsights) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/financials/iworker/ai-insights?${query}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const raw = await res.json();
      setAiInsights(isAiInsightsPayload(raw) ? raw : null);
      setError(null);
    } catch {
      setError("Couldn't load insights.");
      setAiInsights(null);
    } finally {
      setLoaded(true);
    }
  }, [periodInsights, query]);

  useEffect(() => {
    setLoaded(false);
    void load();
  }, [load]);

  const signals = useMemo(
    () => buildIworkerSignals(periodInsights, auditItems),
    [periodInsights, auditItems],
  );

  const data = useMemo(
    () => aiToInsightsData(aiInsights, periodInsights?.selected.label),
    [aiInsights, periodInsights?.selected.label],
  );

  const cards = useMemo(() => {
    const base = buildNoteCards(data, signals, "iworker");
    const ids = new Set(base.map((card) => card.id));
    const aiCards = buildSummaryCards(aiInsights).filter((card) => !ids.has(card.id));
    return [...base, ...aiCards];
  }, [data, signals, aiInsights]);

  const counts = useMemo(() => countByBadge(cards), [cards]);
  const highImpact = counts.find((c) => c.badge === "High impact")?.count ?? 0;

  const regenerate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/iworker/ai-insights/regenerate?${query}`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const raw = await res.json();
      if (!isAiInsightsPayload(raw)) throw new Error("invalid payload");
      setAiInsights(raw);
      if (raw.generated === "failed" || raw.stored === false) {
        setError("Brief generated but could not be saved. It will disappear on refresh.");
      }
    } catch {
      setError("Couldn't generate a new brief. The flags below are still current.");
    } finally {
      setBusy(false);
    }
  }, [query]);

  return {
    data,
    cards,
    counts,
    highImpact,
    loaded: loaded && Boolean(periodInsights),
    busy,
    error,
    regenerate,
  };
}
