"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  badgeForRow,
  badgeForSignal,
  type NoteBadge,
  type NoteRowKind,
  type NoteSource,
} from "./qb-note-badges";
import type { Signal } from "../types/quickbooks";

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

export interface InsightsData {
  status: "ok" | "empty";
  brief: string;
  notes: Record<string, string>;
  /** QuickBooks row lists. Absent on the Teamwork response. */
  chase?: ChaseRow[];
  margin?: MarginRow[];
  hygiene?: HygieneRow[];
  /** Teamwork ships its signals on the insight response; QuickBooks does not. */
  signals?: Signal[];
  /** Teamwork capacity history readiness. */
  history?: { weeks_available?: number; ready?: boolean };
  as_of: string | null;
  generated_at: string | null;
  provider: string | null;
  stale: boolean;
  /** Only present on the regenerate response: "ok" | "failed". */
  generated?: string;
}

export interface NoteCard {
  id: string;
  headline: string;
  detail: string;
  figure?: string;
  badge: NoteBadge;
  /** True when the model wrote a note for this row. */
  aiEnhanced: boolean;
  goTo?: string;
}

/** Badge order for the filter rail — urgency first, bookkeeping last. */
export const BADGE_ORDER: NoteBadge[] = [
  "High impact",
  "Risk",
  "Watch",
  "Opportunity",
  "Action",
];

/**
 * Cash first, then what shapes profit, then the bookkeeping that makes both
 * measurable. Rule-based signals follow — they size the same problems without
 * duplicating a factual row id (chase:… vs ar-late).
 */
export function buildNoteCards(
  data: InsightsData | null,
  signals: Signal[],
  source: NoteSource = "quickbooks",
): NoteCard[] {
  const cards: NoteCard[] = [];
  const notes = data?.notes ?? {};

  for (const row of data?.chase ?? []) {
    const kind: NoteRowKind = "collect";
    const meta = [
      `${row.avg_overdue_days}d overdue`,
      row.overdue_days !== row.avg_overdue_days ? `oldest ${row.overdue_days}d` : null,
      `${row.invoice_count} ${row.invoice_count === 1 ? "invoice" : "invoices"}`,
      row.balance_figure !== row.overdue_figure ? `${row.balance_figure} owed` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const note = notes[row.id];
    cards.push({
      id: row.id,
      headline: row.client,
      detail: [meta, note].filter(Boolean).join(" — "),
      figure: row.overdue_figure,
      badge: badgeForRow(kind, row.slow_payer),
      aiEnhanced: Boolean(note),
    });
  }

  for (const row of data?.margin ?? []) {
    const kind: NoteRowKind = "margin";
    const note = notes[row.id];
    cards.push({
      id: row.id,
      headline: row.label,
      detail: [row.detail, note].filter(Boolean).join(" — "),
      figure: row.figure,
      badge: badgeForRow(kind),
      aiEnhanced: Boolean(note),
    });
  }

  for (const row of data?.hygiene ?? []) {
    const kind: NoteRowKind = "fix";
    const note = notes[row.id];
    cards.push({
      id: row.id,
      headline: row.label,
      detail: note ?? "",
      figure: row.figure,
      badge: badgeForRow(kind),
      aiEnhanced: Boolean(note),
    });
  }

  // Both signal sources, server first. Teamwork derives some cards in the
  // browser and gets others off the insight response; ids collide on purpose
  // where both describe the same fact, and the server's copy wins because it
  // is the one the model was allowed to annotate. QuickBooks passes only the
  // overview list, so this is a plain concat there.
  const rowIds = new Set(cards.map((c) => c.id));
  for (const s of [...(data?.signals ?? []), ...signals]) {
    if (rowIds.has(s.id)) continue;
    // Track ids as they land, not just the row ids seeded above: the two
    // signal lists overlap by design, so the first copy of an id has to block
    // the second.
    rowIds.add(s.id);
    const note = notes[s.id];
    cards.push({
      id: s.id,
      headline: s.headline,
      detail: [s.detail, note].filter(Boolean).join(" — "),
      figure: s.figure,
      badge: badgeForSignal(s.id, s.severity, source),
      aiEnhanced: Boolean(note),
      goTo: s.go_to,
    });
  }

  return cards;
}

/** Cards per badge, in BADGE_ORDER, skipping badges nothing landed on. */
export function countByBadge(cards: NoteCard[]): { badge: NoteBadge; count: number }[] {
  return BADGE_ORDER.map((badge) => ({
    badge,
    count: cards.filter((c) => c.badge === badge).length,
  })).filter((b) => b.count > 0);
}

export interface QbInsights {
  data: InsightsData | null;
  cards: NoteCard[];
  counts: { badge: NoteBadge; count: number }[];
  /** Drives the trigger button's badge. Absent means nothing urgent. */
  highImpact: number;
  loaded: boolean;
  busy: boolean;
  error: string | null;
  regenerate: () => Promise<void>;
}

/**
 * Owns the insights fetch for the whole ledger.
 *
 * Lifted out of the panel that used to render them because two things need the
 * cards now: the drawer that shows them, and the button that has to say how
 * many are urgent before the drawer is ever opened.
 */
export function useQbInsights(
  signals: Signal[],
  source: NoteSource = "quickbooks",
): QbInsights {
  const base = `${API_BASE}/api/v1/financials/${source}/ai-insights`;
  const [data, setData] = useState<InsightsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch(base);
      if (!res.ok) throw new Error(`${res.status}`);
      setData(await res.json());
      setError(null);
    } catch {
      setError("Couldn't load insights.");
    } finally {
      setLoaded(true);
    }
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  const regenerate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${base}/regenerate`, { method: "POST" });
      if (!res.ok) throw new Error(`${res.status}`);
      const next: InsightsData = await res.json();
      setData(next);
      if (next.generated === "failed") {
        setError("Couldn't generate a new brief. The notes below are current.");
      }
    } catch {
      setError("Couldn't generate a new brief. The notes below are current.");
    } finally {
      setBusy(false);
    }
  }, [base]);

  const cards = useMemo(() => buildNoteCards(data, signals, source), [data, signals, source]);
  const counts = useMemo(() => countByBadge(cards), [cards]);
  const highImpact = counts.find((c) => c.badge === "High impact")?.count ?? 0;

  return { data, cards, counts, highImpact, loaded, busy, error, regenerate };
}
