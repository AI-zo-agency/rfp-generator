"use client";

import { useCallback, useMemo, useState } from "react";

import type { AiInsightsData } from "../components/AiInsightsPanel";
import type { AuditItem } from "../components/AuditQueueTable";
import type { PeriodInsights } from "../types/iworker";
import type { Signal } from "../types/quickbooks";
import {
  buildNoteCards,
  countByBadge,
  type InsightsData,
  type QbInsights,
} from "./use-qb-insights";

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
  if (!ai) return null;
  return {
    status: "ok",
    brief: ai.summary.leadership_brief_text,
    notes: {},
    as_of: ai.generated_at,
    generated_at: ai.generated_at,
    provider: ai.provider ?? null,
    model: ai.model ?? null,
    stale: false,
    period_label: ai.stats?.period_label ?? periodLabel ?? null,
  };
}

export function useIworkerInsights(
  periodInsights: PeriodInsights | undefined,
  auditItems: AuditItem[],
  aiInsights: AiInsightsData | null,
  onGenerate: () => Promise<AiInsightsData>,
): QbInsights {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signals = useMemo(
    () => buildIworkerSignals(periodInsights, auditItems),
    [periodInsights, auditItems],
  );

  const data = useMemo(
    () => aiToInsightsData(aiInsights, periodInsights?.selected.label),
    [aiInsights, periodInsights?.selected.label],
  );

  const cards = useMemo(() => buildNoteCards(data, signals, "iworker"), [data, signals]);
  const counts = useMemo(() => countByBadge(cards), [cards]);
  const highImpact = counts.find((c) => c.badge === "High impact")?.count ?? 0;

  const regenerate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await onGenerate();
    } catch {
      setError("Couldn't generate a new brief. The flags below are still current.");
    } finally {
      setBusy(false);
    }
  }, [onGenerate]);

  return {
    data,
    cards,
    counts,
    highImpact,
    loaded: Boolean(periodInsights),
    busy,
    error,
    regenerate,
  };
}
